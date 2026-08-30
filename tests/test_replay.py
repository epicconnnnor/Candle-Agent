"""Replay: pacing, cost control, attribution.

The pacing test is the important one - it asserts bar N+1 is not
published until bar N's analysis has come back, which is the publisher
half of the no-lookahead guarantee. (The reader half, that an analysis
of bar N cannot see bars > N, lives in test_no_lookahead.py and is a
live-path property.)
"""
import asyncio
import json
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_replay.db")

from candle_agent import config, db
from candle_agent.services import replay as replay_svc

from .fake_source import ramp

SYMBOL = "BACKTEST"   # deliberately not "REPLAY": a marker check below
                      # greps the payload, and the symbol must not satisfy it
START_TS = 1_700_000_000_000
STEP = 60_000


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "replay.db"))
    monkeypatch.setattr(config, "ANALYZE_EVERY", 1)
    monkeypatch.setattr(config, "MIN_BARS", 30)
    replay_svc._waiters.clear()
    replay_svc._tasks.clear()
    bars = ramp(120, step_ms=STEP, start_ts=START_TS)
    db.insert_bars(SYMBOL, "1m", bars)
    return bars


class Recorder:
    """Stands in for the bus and, optionally, for the analyzer."""

    def __init__(self, auto_complete=True, delay=0.0):
        self.published: list[tuple[str, dict]] = []
        self.auto_complete = auto_complete
        self.delay = delay
        self.order: list[str] = []

    async def publish(self, subject, payload):
        data = json.loads(payload.decode())
        self.published.append((subject, data))
        if subject.startswith("bars.closed."):
            self.order.append(f"published:{data['ts']}")
            if self.auto_complete:
                asyncio.get_running_loop().create_task(self._complete(data))

    async def _complete(self, bar):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.order.append(f"analysed:{bar['ts']}")
        fut = replay_svc._waiters.get((bar["symbol"], bar["ts"]))
        if fut and not fut.done():
            fut.set_result({"symbol": bar["symbol"], "bar_ts": bar["ts"],
                            "model": "test", "stage1": {}, "stage2": {}})


def install(monkeypatch, rec):
    monkeypatch.setattr(replay_svc, "_js", rec)
    monkeypatch.setattr(replay_svc, "_nc", rec)


def new_run(bars, max_analyses=1000):
    return db.create_replay_run(
        symbol=SYMBOL, interval="1m", start_ts=bars[0]["ts"], end_ts=bars[-1]["ts"],
        status="pending", bars_total=len(bars), max_analyses=max_analyses)


# --- pacing: the publisher half of no-lookahead --------------------------

def test_next_bar_is_not_published_until_the_previous_is_analysed(monkeypatch, clean):
    rec = Recorder(auto_complete=True, delay=0.02)
    install(monkeypatch, rec)
    window = clean[60:66]
    run_id = new_run(window)

    asyncio.run(replay_svc._run(run_id, SYMBOL, "1m", window, 1000))

    # strictly alternating: publish, analyse, publish, analyse ...
    assert rec.order == [
        item
        for bar in window
        for item in (f"published:{bar['ts']}", f"analysed:{bar['ts']}")
    ], rec.order


def test_a_slow_analysis_makes_the_replay_wait(monkeypatch, clean):
    """If the model is slower than the replay, the replay slows down."""
    rec = Recorder(auto_complete=True, delay=0.15)
    install(monkeypatch, rec)
    window = clean[60:63]
    run_id = new_run(window)

    started = asyncio.get_event_loop_policy().new_event_loop().time
    del started
    import time as _t
    t0 = _t.time()
    asyncio.run(replay_svc._run(run_id, SYMBOL, "1m", window, 1000))
    elapsed = _t.time() - t0

    assert elapsed >= 0.15 * len(window), f"replay outran the analyses ({elapsed:.2f}s)"
    assert db.get_replay_run(run_id)["status"] == "completed"


def test_a_missing_analysis_aborts_rather_than_racing_on(monkeypatch, clean):
    rec = Recorder(auto_complete=False)          # nothing ever completes
    install(monkeypatch, rec)
    monkeypatch.setattr(replay_svc, "COMPLETION_TIMEOUT_S", 0.05)
    window = clean[60:66]
    run_id = new_run(window)

    asyncio.run(replay_svc._run(run_id, SYMBOL, "1m", window, 1000))

    run = db.get_replay_run(run_id)
    assert run["status"] == "failed"
    assert "no analysis returned" in run["detail"]
    # exactly one bar published: it did not charge ahead
    assert len([s for s, _ in rec.published if s.startswith("bars.closed")]) == 1


# --- the published payload is indistinguishable from live ----------------

def test_published_bars_look_exactly_like_ingest_bars(monkeypatch, clean):
    rec = Recorder()
    install(monkeypatch, rec)
    window = clean[60:62]
    asyncio.run(replay_svc._run(new_run(window), SYMBOL, "1m", window, 1000))

    subject, payload = next((s, p) for s, p in rec.published
                            if s.startswith("bars.closed"))
    assert subject == f"bars.closed.{SYMBOL}"
    assert set(payload) == {"symbol", "ts", "open", "high", "low", "close", "volume"}
    assert "replay" not in json.dumps(payload).lower()


# --- cost control --------------------------------------------------------

def test_run_stops_at_the_cap_and_is_marked_partial(monkeypatch, clean):
    rec = Recorder()
    install(monkeypatch, rec)
    window = clean[60:80]
    run_id = new_run(window, max_analyses=5)

    asyncio.run(replay_svc._run(run_id, SYMBOL, "1m", window, 5))

    run = db.get_replay_run(run_id)
    assert run["status"] == "partial"
    assert run["analyses_done"] == 5
    assert "cap of 5" in run["detail"]


def test_start_is_refused_without_max_analyses(clean):
    parsed, error = replay_svc._validate({"symbol": SYMBOL, "interval": "1m"})
    assert parsed is None
    assert "max_analyses is required" in error


def test_start_is_refused_when_analyze_every_is_not_one(monkeypatch, clean):
    monkeypatch.setattr(config, "ANALYZE_EVERY", 3)
    parsed, error = replay_svc._validate(
        {"symbol": SYMBOL, "interval": "1m", "max_analyses": 10})
    assert parsed is None
    assert "ANALYZE_EVERY=1" in error


def test_start_is_refused_without_enough_warmup_history(monkeypatch, clean):
    """The analyzer needs MIN_BARS before the first replayed bar."""
    parsed, error = replay_svc._validate({
        "symbol": SYMBOL, "interval": "1m", "max_analyses": 10,
        "start": START_TS, "end": START_TS + 10 * STEP})
    assert parsed is None
    assert "bars exist before the window" in error


def test_estimate_uses_measured_tokens_when_available(clean):
    db.insert_analysis(SYMBOL, 1, {"regime": "range"}, {"decision": "no_trade"},
                       "deepseek-chat", 100, interval="1m",
                       prompt_tokens=1500, completion_tokens=300)
    est = replay_svc.estimate(bars_total=100, max_analyses=40, model="deepseek-chat")

    assert est["analyses_planned"] == 40
    assert est["tokens_per_analysis"] == 1800
    assert est["estimated_tokens"] == 72_000
    assert "measured from 1" in est["basis"]


def test_estimate_falls_back_and_says_so(clean):
    est = replay_svc.estimate(bars_total=10, max_analyses=10, model="never-seen")
    assert est["estimated_tokens"] == 10 * replay_svc.ASSUMED_TOKENS_PER_ANALYSIS
    assert "no measured usage" in est["basis"]


# --- attribution ---------------------------------------------------------

def test_analyses_and_trades_written_during_a_run_are_stamped(monkeypatch, clean):
    from candle_agent import paper

    pre = db.insert_analysis(SYMBOL, 0, {"regime": "range"}, {"decision": "no_trade"},
                             "live", 1, interval="1m")
    analysis_floor = db.max_id("analyses")
    trade_floor = db.max_id("paper_trades")

    during = db.insert_analysis(SYMBOL, 1, {"regime": "range"},
                                {"decision": "no_trade"}, "replayed", 1, interval="1m")
    trade = paper.trade_from_decision(
        SYMBOL, {"decision": "buy_limit", "entry": 10.0, "stop": 9.0, "target": 12.0}, 1)
    trade["id"] = db.save_trade(trade)

    run_id = new_run(clean[60:62])
    stamped_a, stamped_t = db.stamp_replay_rows(run_id, SYMBOL, analysis_floor, trade_floor)

    assert (stamped_a, stamped_t) == (1, 1)
    import sqlite3
    c = sqlite3.connect(db.db_path())
    try:
        assert c.execute("SELECT replay_run_id FROM analyses WHERE id=?",
                         (during,)).fetchone()[0] == run_id
        assert c.execute("SELECT replay_run_id FROM analyses WHERE id=?",
                         (pre,)).fetchone()[0] is None      # predates the run
        assert c.execute("SELECT replay_run_id FROM paper_trades WHERE id=?",
                         (trade["id"],)).fetchone()[0] == run_id
    finally:
        c.close()


def test_live_analyses_keep_a_null_replay_run_id(clean):
    db.insert_analysis(SYMBOL, 5, {"regime": "range"}, {"decision": "no_trade"},
                       "live", 1, interval="1m")
    assert db.latest_analysis(SYMBOL, "1m")["replay_run_id"] is None


def test_stop_request_is_honoured_between_bars(monkeypatch, clean):
    rec = Recorder()
    install(monkeypatch, rec)
    window = clean[60:80]
    run_id = new_run(window)

    original = replay_svc._await_analysis

    async def stop_after_two(symbol, bar_ts, timeout):
        result = await original(symbol, bar_ts, timeout)
        if db.get_replay_run(run_id)["bars_done"] >= 1:
            db.request_replay_stop(run_id)
        return result

    monkeypatch.setattr(replay_svc, "_await_analysis", stop_after_two)
    asyncio.run(replay_svc._run(run_id, SYMBOL, "1m", window, 1000))

    run = db.get_replay_run(run_id)
    assert run["status"] == "stopped"
    assert run["bars_done"] < len(window)


def test_no_explicit_start_spends_leading_bars_as_warmup(clean):
    """The common case - 'replay this symbol' - must not error just because
    the first stored bar has no history behind it."""
    parsed, error = replay_svc._validate(
        {"symbol": SYMBOL, "interval": "1m", "max_analyses": 10})

    assert error is None, error
    assert len(parsed["bars"]) == 120 - config.MIN_BARS
    assert parsed["bars"][0]["ts"] == START_TS + config.MIN_BARS * STEP


def test_too_few_bars_to_warm_up_is_still_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tiny.db"))
    monkeypatch.setattr(config, "ANALYZE_EVERY", 1)
    monkeypatch.setattr(config, "MIN_BARS", 30)
    db.insert_bars("TINY", "1m", ramp(10, step_ms=STEP, start_ts=START_TS))

    parsed, error = replay_svc._validate(
        {"symbol": "TINY", "interval": "1m", "max_analyses": 5})
    assert parsed is None
    assert "at least 31 are needed" in error


def test_config_exposes_every_field_the_replay_service_reads():
    """Guards against the runtime AttributeError this actually hit: the
    service referenced config.LLM_MODEL before it existed."""
    for field in ("LLM_MODEL", "LLM_PROVIDER", "ANALYZE_EVERY", "MIN_BARS", "INTERVAL"):
        assert hasattr(config, field), f"config is missing {field}"
