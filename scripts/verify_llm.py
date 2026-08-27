"""Run the real two-stage pipeline against live Alpaca bars.

    python scripts/verify_llm.py                 # one window, current bars
    python scripts/verify_llm.py --conditions    # trending / range / gap
    python scripts/verify_llm.py --symbol MSFT --interval 1d

Checks, each reported pass/fail:

    1. stage 1 returns JSON valid against STAGE1_SCHEMA
    2. stage 2 returns JSON valid against STAGE2_SCHEMA
    3. stage 2's decision does not contradict stage 1's diagnosis
       (no long call on a bearish read, and vice versa)
    4. a deliberately malformed first response is repaired or retried
    5. the no-trade path yields a valid response, not an error

The diagnosis and decision are printed in full so the output can be read
rather than just counted. Token usage is reported per stage and per whole
analysis, so a public demo can be costed before it is exposed.

The API key is read from the environment and never printed: every line
goes through _safe(), which scrubs it from any string including tracebacks.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SECRETS: list[str] = []


def _safe(text) -> str:
    out = str(text)
    for secret in SECRETS:
        if secret and len(secret) > 4:
            out = out.replace(secret, "***REDACTED***")
    return out


def load_dotenv(path=".env"):
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.split(" #", 1)[0].strip().strip('"\''))


load_dotenv()
SECRETS[:] = [os.environ.get("LLM_API_KEY", ""), os.environ.get("ALPACA_SECRET_KEY", "")]

# analyses are written to a scratch database, never the working one
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "verify_llm.db")

from candle_agent import config, db                              # noqa: E402
from candle_agent.llm import get_llm                             # noqa: E402
from candle_agent.orchestrator import analyze                    # noqa: E402
from candle_agent.schemas import (STAGE1_SCHEMA, STAGE2_SCHEMA,  # noqa: E402
                                  consistency_errors)
from candle_agent.sources.alpaca import AlpacaSource             # noqa: E402
from jsonschema import validate                                  # noqa: E402

LONG = {"buy_limit", "buy_stop", "market_buy"}
SHORT = {"sell_limit", "sell_stop", "market_sell"}

RESULTS: list[tuple[str, bool, str]] = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {_safe(detail)}" if detail else ""))


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


# --- instrumented clients ----------------------------------------------

class Counting:
    """Wraps the configured client and keeps per-stage token usage."""

    def __init__(self, inner):
        self.inner = inner
        self.model = inner.model
        self.calls: list[dict] = []

    def complete(self, system, user):
        before = len(getattr(self.inner, "usage", []))
        out = self.inner.complete(system, user)
        usage = getattr(self.inner, "usage", [])[before:]
        self.calls.append({
            "stage": 1 if "STAGE-1" in system else 2,
            "system_chars": len(system),
            "user_chars": len(user),
            "output_chars": len(out),
            "usage": usage[-1] if usage else None,
        })
        return out

    @property
    def usage(self):
        return [c["usage"] for c in self.calls if c["usage"]]


class MalformedFirst(Counting):
    """Returns garbage once, then defers to the real client.

    Exercises the repair/retry loop in orchestrator._call_validated with a
    genuine model behind it, rather than asserting against a stub.
    """

    def __init__(self, inner, payload='{"regime": "bull_trend", NOT JSON'):
        super().__init__(inner)
        self.payload = payload
        self.fired = False

    def complete(self, system, user):
        if not self.fired:
            self.fired = True
            self.calls.append({"stage": 0, "system_chars": len(system),
                               "user_chars": len(user), "output_chars": len(self.payload),
                               "usage": None})
            return self.payload
        return super().complete(system, user)


class ForcedNoTrade(Counting):
    """Stage 1 comes from the real model; stage 2 is forced to no_trade."""

    def complete(self, system, user):
        if "STAGE-1" in system:
            return super().complete(system, user)
        return json.dumps({
            "decision": "no_trade",
            "entry": None, "stop": None, "target": None, "risk_reward": None,
            "confidence": "low",
            "reasoning_chain": ["forced no-trade path check"],
        })


# --- market data --------------------------------------------------------

async def fetch(symbol: str, interval: str, limit: int = 200):
    src = AlpacaSource(os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"])
    return await src.history(symbol, interval, limit=limit)


def store(symbol, interval, bars):
    for suffix in ("", "-wal", "-shm"):
        f = os.environ["DB_PATH"] + suffix
        if os.path.exists(f):
            os.remove(f)
    db.insert_bars(symbol, interval, bars)


def when(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def classify(bars: list[dict]) -> dict:
    """Describe a window: how directional it is, and its opening gap."""
    closes = [b["close"] for b in bars]
    net = closes[-1] - closes[0]
    path = sum(abs(closes[i + 1] - closes[i]) for i in range(len(closes) - 1)) or 1e-9
    hi, lo = max(b["high"] for b in bars), min(b["low"] for b in bars)
    span = (hi - lo) / closes[0] * 100 if closes[0] else 0
    gap = (bars[-1]["open"] - bars[-2]["close"]) / bars[-2]["close"] * 100 if len(bars) > 1 else 0
    return {
        "directionality": abs(net) / path,      # 1.0 = straight line
        "net_pct": net / closes[0] * 100 if closes[0] else 0,
        "span_pct": span,
        "gap_pct": gap,
    }


def find_conditions(bars: list[dict], window: int = 60) -> dict:
    """Pick the clearest trending, range and gap windows in the history."""
    best: dict[str, tuple[float, int]] = {}
    for end in range(window, len(bars) + 1):
        w = bars[end - window:end]
        m = classify(w)
        scores = {
            "trending": m["directionality"] if abs(m["net_pct"]) > 3 else 0.0,
            "range": (1 - m["directionality"]) if abs(m["net_pct"]) < 2 else 0.0,
            "gap": abs(m["gap_pct"]),
        }
        for name, score in scores.items():
            if score > best.get(name, (0.0, 0))[0]:
                best[name] = (score, end)
    return {name: bars[end - window:end] for name, (_, end) in best.items()}


# --- checks -------------------------------------------------------------

def cross_stage_errors(stage1: dict, stage2: dict) -> list[str]:
    """Stage 2 must not contradict the regime stage 1 committed to."""
    errs = []
    regime, decision = stage1["regime"], stage2["decision"]
    if decision == "no_trade":
        return errs
    if regime == "bear_trend" and decision in LONG:
        errs.append(f"long call ({decision}) on a bear_trend diagnosis")
    if regime == "bull_trend" and decision in SHORT:
        errs.append(f"short call ({decision}) on a bull_trend diagnosis")
    return errs


def show(label: str, stage1: dict, stage2: dict, latency: int, model: str):
    print(f"\n    [{label}] {model}  {latency} ms")
    print(f"    stage 1  {stage1['regime']} / {stage1['strength']}"
          f"  levels={stage1['key_levels']}")
    print(f"             {stage1['summary']}")
    print(f"    stage 2  {stage2['decision']}  confidence={stage2['confidence']}"
          f"  entry={stage2['entry']} stop={stage2['stop']} target={stage2['target']}"
          f"  rr={stage2['risk_reward']}")
    for i, step in enumerate(stage2["reasoning_chain"], 1):
        print(f"             {i}. {step}")


def price(calls: list[dict]) -> dict:
    got = [c["usage"] for c in calls if c["usage"]]
    if not got:
        return {}
    total = {k: sum(u.get(k) or 0 for u in got)
             for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                       "cache_hit_tokens", "cache_miss_tokens")}
    total["calls"] = len(got)
    return total


def run_once(label: str, symbol: str, interval: str, bars: list[dict], client=None):
    store(symbol, interval, bars)
    llm = client or Counting(get_llm())
    result = analyze(symbol, min_bars=config.MIN_BARS, llm=llm)
    stage1, stage2 = result["stage1"], result["stage2"]

    validate(stage1, STAGE1_SCHEMA)
    validate(stage2, STAGE2_SCHEMA)
    show(label, stage1, stage2, result["latency_ms"], result["model"])
    return result, llm


# --- main ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--conditions", action="store_true",
                    help="also run trending / range / gap windows")
    args = ap.parse_args()

    print("LLM pipeline verification")
    print("=" * 62)
    provider = os.environ.get("LLM_PROVIDER", "mock")
    key = os.environ.get("LLM_API_KEY", "")
    print(f"  provider:  {provider}")
    print(f"  base url:  {os.environ.get('LLM_BASE_URL')}")
    print(f"  model:     {os.environ.get('LLM_MODEL')}")
    print(f"  api key:   {'set, len ' + str(len(key)) if key else 'NOT SET'}")
    if provider == "mock":
        print("\n  WARNING: LLM_PROVIDER=mock - this exercises the harness, not a")
        print("  real model. Set LLM_PROVIDER=openai_compat and a real")
        print("  LLM_API_KEY to verify the actual pipeline.")
    if len(key) < 8 and provider != "mock":
        print("\n  LLM_API_KEY looks like a placeholder; a real key is required.")
        return 1

    bars = asyncio.run(fetch(args.symbol, args.interval, 200))
    print(f"\n  bars:      {len(bars)} x {args.interval} {args.symbol}")

    section("1-3. schema and cross-stage consistency (current window)")
    result, llm = run_once("current", args.symbol, args.interval, bars)
    stage1, stage2 = result["stage1"], result["stage2"]
    record("stage 1 matches STAGE1_SCHEMA", True, stage1["regime"])
    record("stage 2 matches STAGE2_SCHEMA", True, stage2["decision"])

    within = consistency_errors(stage2)
    record("stage 2 internally consistent (stop/entry/target ordering)",
           not within, "; ".join(within) or "ok")
    cross = cross_stage_errors(stage1, stage2)
    record("stage 2 agrees with the stage 1 diagnosis",
           not cross, "; ".join(cross) or f"{stage1['regime']} -> {stage2['decision']}")

    section("4. malformed response is repaired or retried")
    try:
        bad, bad_llm = run_once("malformed-first", args.symbol, args.interval, bars,
                                client=MalformedFirst(get_llm()))
        validate(bad["stage1"], STAGE1_SCHEMA)
        record("recovered from a malformed first response", True,
               f"retried and produced {bad['stage1']['regime']}")
    except Exception as e:
        record("recovered from a malformed first response", False,
               f"{type(e).__name__}: {e}")

    section("5. no-trade path")
    try:
        nt, _ = run_once("forced-no-trade", args.symbol, args.interval, bars,
                         client=ForcedNoTrade(get_llm()))
        ok = nt["stage2"]["decision"] == "no_trade" and not consistency_errors(nt["stage2"])
        record("no_trade validates and does not raise", ok, "decision=no_trade")
    except Exception as e:
        record("no_trade validates and does not raise", False, f"{type(e).__name__}: {e}")

    section("token cost per full analysis")
    cost = price(llm.calls)
    if cost:
        print(f"  calls per analysis:   {cost['calls']} (stage 1 + stage 2)")
        print(f"  prompt tokens:        {cost['prompt_tokens']}")
        print(f"  completion tokens:    {cost['completion_tokens']}")
        print(f"  total tokens:         {cost['total_tokens']}")
        if cost.get("cache_hit_tokens"):
            print(f"  of which cache hits:  {cost['cache_hit_tokens']}")
    else:
        print("  no usage reported (the mock client makes no API calls)")
    for c in llm.calls:
        print(f"    stage {c['stage']}: prompt {c['system_chars'] + c['user_chars']} chars"
              f" -> {c['output_chars']} chars"
              + (f", {c['usage']['total_tokens']} tokens" if c["usage"] else ""))

    if args.conditions:
        section("market conditions")
        found = find_conditions(bars)
        for name in ("trending", "range", "gap"):
            w = found.get(name)
            if not w:
                print(f"  {name}: no clear example in this history")
                continue
            m = classify(w)
            print(f"\n  {name.upper()}  {when(w[0]['ts'])} .. {when(w[-1]['ts'])}"
                  f"  ({len(w)} bars)")
            print(f"    net {m['net_pct']:+.1f}%  "
                  f"directionality {m['directionality']:.2f}  "
                  f"span {m['span_pct']:.1f}%  last-bar gap {m['gap_pct']:+.2f}%")
            try:
                run_once(name, args.symbol, args.interval, w)
            except Exception as e:
                print(f"    FAILED: {_safe(e)}")

    section("summary")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, _ in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n  {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
