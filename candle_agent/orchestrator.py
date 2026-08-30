"""Two-stage analysis: diagnose -> route strategy prompt -> decide.
Every LLM output is validated against a JSON schema plus consistency
checks; invalid output triggers a retry with the error fed back."""
import hashlib
import json
import pathlib
import time
import types

from jsonschema import validate, ValidationError

from . import db
from .features import build_feature_packet
from .llm import get_llm
from . import schemas
from .schemas import (STAGE1_SCHEMA, STAGE2_SCHEMA, consistency_errors,
                      risk_reward, stage1_consistency_errors)

PROMPTS = pathlib.Path(__file__).parent / "prompts"

# Prompts that are NOT part of the analysis contract. Everything else in
# the directory is, and is fingerprinted.
#
# Stated as an exclusion rather than a pattern on purpose. A pattern fails
# unsafe: a new contract prompt named outside it is silently uncovered,
# and the first symptom is a pooled sample that was quietly two
# populations. This way a new file is covered by default, and the worst
# case is an unnecessary reset rather than a wrong number.
NON_CONTRACT_PROMPTS = frozenset({"followup_chat.txt"})
MAX_RETRIES = 2

ROUTES = {
    "bull_trend": "stage2_trend.txt",
    "bear_trend": "stage2_trend.txt",
    "range": "stage2_range.txt",
    "chop": "stage2_range.txt",
}


def _prompt(name):
    return (PROMPTS / name).read_text()


def prompt_fingerprint() -> str:
    """Identity of the contract an analysis was produced under.

    Covers EVERY prompt in the directory, not just the one this route
    happened to take, plus both schemas and the validator's own gate. Two
    consequences, and both are the point:

    - Two analyses from the same code state share a fingerprint whatever
      regime they diagnosed, so a score run may still pool a `range`
      verdict with a `bull_trend` one. Hashing only the routed prompt
      would have split a sample by its own answers.
    - Editing any prompt, adding a schema field, or moving the
      risk-reward, level-proximity or stop-distance gate changes it,
      because each of those changes what the model was asked or what it
      was held to. A score run that pools
      across such a change is pooling two different questions, and
      services/scorer.py refuses it.

    Both halves are enumerated, not listed. Listing them by hand is how
    this hash has been wrong twice: once when it globbed every *.txt and
    swept in a prompt that was not part of the contract, once when it
    hashed only the first of three validator gates. Neither could be
    caught by reading it, because an omission looks exactly like a hash
    that has not changed.
    """
    h = hashlib.sha256()
    for path in contract_prompts():
        h.update(path.name.encode())
        h.update(path.read_bytes())
    for name, value in validator_gates():
        h.update(f"{name}={value}".encode())
    return h.hexdigest()[:16]


def contract_prompts() -> list[pathlib.Path]:
    """Every prompt file that forms part of the analysis contract."""
    return sorted(p for p in PROMPTS.glob("*.txt")
                  if p.name not in NON_CONTRACT_PROMPTS)


def _canonical(value) -> str:
    """A stable string for any constant a schema module might hold.

    Sets have no order, so they are sorted before serialising - otherwise
    the fingerprint would change between interpreter runs and every score
    run would refuse to pool with itself.
    """
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    return json.dumps(value, sort_keys=True, default=str)


def validator_gates() -> list[tuple[str, str]]:
    """Every module-level constant in schemas.py, canonically serialised.

    Enumerated from the module rather than named one by one, so a gate
    added later is covered the moment it exists. Anything a reply is
    validated against belongs here: the schemas themselves, the numeric
    floors, and the checklist's own node and answer vocabularies.
    """
    out = []
    for name, value in sorted(vars(schemas).items()):
        if name.startswith("__") or not name.lstrip("_").isupper():
            continue
        if callable(value) or isinstance(value, types.ModuleType):
            continue
        out.append((name, _canonical(value)))
    return out


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


def analyze(symbol: str, min_bars: int = 30, llm=None, on_event=None,
            as_of_ts: int | None = None):
    """Run the two-stage pipeline.

    `llm` defaults to the configured client; pass one to instrument the
    call (token accounting) or to script a response in a test.

    `on_event(name, payload)` is called as each stage lands, so a caller
    can publish progress at the moment it happens. It is synchronous and
    must not block - services bridge it back to the event loop. Emitting
    from here rather than from build_feature_packet keeps features.py a
    pure function with no I/O; it is the same instant either way.

    `as_of_ts` bounds the bars this analysis may see. Replay needs it
    because the whole history is already stored, but it is a live
    correctness fix too: without it, an analysis of bar N read whatever
    was newest, which is a later bar whenever the analyzer lagged ingest
    or JetStream redelivered the message.
    """
    # recent_bars resolves to the symbol's most recently ingested interval,
    # so the analyzer never has to know which one is live
    bars = db.recent_bars(symbol, limit=100, as_of_ts=as_of_ts)
    if len(bars) < min_bars:
        raise RuntimeError(f"need at least {min_bars} bars, have {len(bars)}")
    interval = bars[-1].get("interval", "1m")

    emit = on_event or (lambda name, payload: None)

    packet = build_feature_packet(bars)
    emit("snapshot.built", {
        "symbol": symbol,
        "interval": interval,
        "bars": len(bars),
        "first_ts": bars[0]["ts"],
        "last_ts": bars[-1]["ts"],
    })
    user_ctx = (
        f"symbol: {symbol}\n"
        f"EMA20: {packet['ema20']} (price {packet['price_vs_ema']})\n"
        f"ATR14: {packet['atr14']}\n"
        f"last close: {packet['last_close']}\n\n"
        f"Bar table (K1 = newest closed bar):\n{packet['bar_table']}"
    )

    llm = llm or get_llm()
    t0 = time.time()

    stage1 = _call_validated(llm, _prompt("stage1_diagnose.txt"), user_ctx,
                             STAGE1_SCHEMA,
                             extra_check=stage1_consistency_errors)
    # validated, and before stage 2 has started
    emit("analysis.stage1.completed", {
        "symbol": symbol,
        "interval": interval,
        "bar_ts": bars[-1]["ts"],
        "stage1": stage1,
    })

    route = ROUTES[stage1["regime"]]
    stage2_user = f"Stage-1 diagnosis:\n{json.dumps(stage1)}\n\n{user_ctx}"
    # the checklist is checked against the geometry, so the check needs the
    # same numbers the model was shown - the diagnosis it must agree with,
    # the ATR its stop is measured in, and the price level_proximity falls
    # back to when there is no entry
    stage2 = _call_validated(
        llm, _prompt(route), stage2_user, STAGE2_SCHEMA,
        extra_check=lambda d: consistency_errors(
            d, stage1, packet["atr14"], packet["last_close"]),
    )

    # The model reports risk_reward as well as the prices, and the two do
    # not always agree - one of the six trades in score run 6 claimed 2.0 on
    # geometry worth 1.889. The gate already uses the derived value; store it
    # too, so the row, the chart and any later reader cannot disagree with
    # the entry/stop/target sitting beside them. None for no_trade.
    stage2["risk_reward"] = risk_reward(
        stage2.get("entry"), stage2.get("stop"), stage2.get("target"))

    latency_ms = int((time.time() - t0) * 1000)
    # measured usage, so a replay can be costed from history not guesswork
    usage = [u for u in getattr(llm, "usage", []) if u]
    prompt_tokens = sum(u.get("prompt_tokens") or 0 for u in usage) or None
    completion_tokens = sum(u.get("completion_tokens") or 0 for u in usage) or None
    db.insert_analysis(symbol, bars[-1]["ts"], stage1, stage2, llm.model,
                       latency_ms, interval=interval,
                       # the same numbers the model was shown
                       price_at=packet["last_close"], atr_at=packet["atr14"],
                       # the backward half of the cycle grader's amplitude
                       # ratio, stored because the scorer may not look back
                       envelope_at=packet["envelope_atr"],
                       prompt_tokens=prompt_tokens,
                       completion_tokens=completion_tokens,
                       prompt_fingerprint=prompt_fingerprint())
    return {"stage1": stage1, "stage2": stage2, "model": llm.model, "latency_ms": latency_ms}
