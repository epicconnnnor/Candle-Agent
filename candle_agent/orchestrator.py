"""Two-stage analysis: diagnose -> route strategy prompt -> decide.
Every LLM output is validated against a JSON schema plus consistency
checks; invalid output triggers a retry with the error fed back."""
import json
import pathlib
import time

from jsonschema import validate, ValidationError

from . import db
from .features import build_feature_packet
from .llm import get_llm
from .schemas import STAGE1_SCHEMA, STAGE2_SCHEMA, consistency_errors

PROMPTS = pathlib.Path(__file__).parent / "prompts"
MAX_RETRIES = 2

ROUTES = {
    "bull_trend": "stage2_trend.txt",
    "bear_trend": "stage2_trend.txt",
    "range": "stage2_range.txt",
    "chop": "stage2_range.txt",
}


def _prompt(name):
    return (PROMPTS / name).read_text()


def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _call_validated(llm, system, user, schema, extra_check=None):
    """Call the LLM, validate JSON against schema (+ optional consistency
    check). On failure, retry with the error appended to the user prompt."""
    last_err = None
    for _ in range(1 + MAX_RETRIES):
        raw = llm.complete(system, user if not last_err else user + f"\n\nYour previous output was invalid: {last_err}. Fix it and output ONLY valid JSON.")
        try:
            obj = json.loads(_strip_fences(raw))
            validate(obj, schema)
            if extra_check:
                errs = extra_check(obj)
                if errs:
                    raise ValidationError("; ".join(errs))
            return obj
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = str(e)[:300]
    raise RuntimeError(f"LLM output failed validation after retries: {last_err}")


def analyze(symbol: str, min_bars: int = 30):
    # recent_bars resolves to the symbol's most recently ingested interval,
    # so the analyzer never has to know which one is live
    bars = db.recent_bars(symbol, limit=100)
    if len(bars) < min_bars:
        raise RuntimeError(f"need at least {min_bars} bars, have {len(bars)}")
    interval = bars[-1].get("interval", "1m")

    packet = build_feature_packet(bars)
    user_ctx = (
        f"symbol: {symbol}\n"
        f"EMA20: {packet['ema20']} (price {packet['price_vs_ema']})\n"
        f"ATR14: {packet['atr14']}\n"
        f"last close: {packet['last_close']}\n\n"
        f"Bar table (K1 = newest closed bar):\n{packet['bar_table']}"
    )

    llm = get_llm()
    t0 = time.time()

    stage1 = _call_validated(llm, _prompt("stage1_diagnose.txt"), user_ctx, STAGE1_SCHEMA)

    route = ROUTES[stage1["regime"]]
    stage2_user = f"Stage-1 diagnosis:\n{json.dumps(stage1)}\n\n{user_ctx}"
    stage2 = _call_validated(
        llm, _prompt(route), stage2_user, STAGE2_SCHEMA, extra_check=consistency_errors
    )

    latency_ms = int((time.time() - t0) * 1000)
    db.insert_analysis(symbol, bars[-1]["ts"], stage1, stage2, llm.model,
                       latency_ms, interval=interval)
    return {"stage1": stage1, "stage2": stage2, "model": llm.model, "latency_ms": latency_ms}
