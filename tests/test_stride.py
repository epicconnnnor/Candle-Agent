"""Striding the publisher must not stride what the model reads.

A replay at stride 10 publishes bars 30, 40, 50, ... and analyses only
those. The claim this file exists to defend is that the analysis of bar
50 still sees bars 21..50 - contiguous, including the nine bars the
publisher skipped - because the analyzer takes its history from
db.recent_bars(as_of_ts=...), which reads the bars table, not the stream.

If that claim were false, stride would silently degrade every analysis it
touched: the model would be reasoning about a series with holes in it and
nothing in the output would say so.

Why stride exists at all: scoring walks a fixed window forward from each
analysis. At stride 1 those windows overlap almost completely, so 25
consecutive analyses are nowhere near 25 independent observations.
"""
import json
import os
import sqlite3
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_stride.db")

from candle_agent import config, db
from candle_agent.orchestrator import analyze
from candle_agent.services import replay as replay_svc

from .fake_source import ramp

SYMBOL = "STRIDE"
STEP_MS = 60_000
START_TS = 1_700_000_000_000
MIN_BARS = 30


@pytest.fixture(autouse=True)
def series(monkeypatch, tmp_path):
    """200 contiguous bars, every one stored."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "stride.db"))
    monkeypatch.setattr(config, "ANALYZE_EVERY", 1)
    monkeypatch.setattr(config, "MIN_BARS", MIN_BARS)
    bars = ramp(200, start=100.0, step=0.5, step_ms=STEP_MS, start_ts=START_TS)
    db.insert_bars(SYMBOL, "1m", bars)
    return bars


def ts_of(index: int) -> int:
    return START_TS + index * STEP_MS


class RecordingLLM:
    """Captures the exact prompt text each stage was shown."""

    model = "recording"

    def __init__(self):
        self.usage: list[dict] = []
        self.prompts: list[str] = []

    def complete(self, system, user):
        self.prompts.append(user)
        if "STAGE-1" in system:
            return json.dumps({"regime": "range", "cycle": "compression",
                               "strength": "weak",
                               "key_levels": [100.0], "summary": "flat"})
        return json.dumps({"decision": "no_trade", "entry": None, "stop": None,
                           "target": None, "risk_reward": None,
                           "confidence": "low", "reasoning_chain": ["none"],
                           "decision_path": [
                    {"node": "trend_alignment", "answer": "na", "because": "no trade"},
                    {"node": "level_proximity", "answer": "mid_range", "because": "no trade"},
                    {"node": "stop_placement", "answer": "na", "because": "no trade"},
                    {"node": "risk_reward", "answer": "na", "because": "no trade"}]})


# --- which bars get published -------------------------------------------

def test_default_stride_publishes_every_bar(series):
    """The pre-stride behaviour, unchanged."""
    parsed, error = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                          "max_analyses": 1000})
    assert error is None
    assert parsed["stride"] == 1
    # 200 bars, the first MIN_BARS spent as warmup
    assert len(parsed["bars"]) == 200 - MIN_BARS
    assert parsed["bars"][0]["ts"] == ts_of(MIN_BARS)


def test_stride_selects_every_nth_bar_after_the_warmup(series):
    parsed, error = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                          "max_analyses": 1000, "stride": 10})
    assert error is None
    got = [b["ts"] for b in parsed["bars"]]
    assert got == [ts_of(i) for i in range(MIN_BARS, 200, 10)]
    # the first analysed bar is unchanged by striding: it is still the
    # first bar that has enough history behind it
    assert got[0] == ts_of(MIN_BARS)


def test_stride_shrinks_the_cost_estimate(series):
    dense, _ = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                     "max_analyses": 1000})
    sparse, _ = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                      "max_analyses": 1000, "stride": 10})
    dense_est = replay_svc.estimate(len(dense["bars"]), 1000, None)
    sparse_est = replay_svc.estimate(len(sparse["bars"]), 1000, None)
    assert sparse_est["analyses_planned"] == 17
    assert sparse_est["estimated_tokens"] < dense_est["estimated_tokens"]


@pytest.mark.parametrize("bad", [0, -1, "half"])
def test_a_stride_below_one_is_refused(series, bad):
    parsed, error = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                          "max_analyses": 10, "stride": bad})
    assert parsed is None
    assert "stride" in error


# --- the claim: history stays contiguous --------------------------------

def test_every_strided_decision_still_reads_a_contiguous_history(series):
    """For each published bar, the 30 bars behind it are consecutive -
    no holes where the publisher skipped."""
    parsed, _ = replay_svc._validate({"symbol": SYMBOL, "interval": "1m",
                                      "max_analyses": 1000, "stride": 10})

    for bar in parsed["bars"]:
        window = db.recent_bars(SYMBOL, limit=MIN_BARS, interval="1m",
                                as_of_ts=bar["ts"])
        assert len(window) == MIN_BARS
        assert window[-1]["ts"] == bar["ts"], "window must end on the decision bar"
        gaps = [b["ts"] - a["ts"] for a, b in zip(window, window[1:])]
        assert set(gaps) == {STEP_MS}, f"history has holes: {sorted(set(gaps))}"


def test_the_model_is_shown_the_bars_the_publisher_skipped(series):
    """End to end through the real analysis path: analysing bar 50 in a
    stride-10 run, bars 41..49 were never published and must still be in
    the prompt."""
    llm = RecordingLLM()
    analyze(SYMBOL, min_bars=MIN_BARS, llm=llm, as_of_ts=ts_of(50))
    shown = "\n".join(llm.prompts)

    skipped = {series[i]["close"] for i in range(41, 50)}
    missing = sorted(c for c in skipped if str(c) not in shown)
    assert not missing, f"skipped bars never reached the model: {missing}"


def test_and_still_no_bar_after_the_decision(series):
    """The vacuity guard for the test above: 'the model sees everything'
    would pass it too. Future bars must remain invisible."""
    llm = RecordingLLM()
    analyze(SYMBOL, min_bars=MIN_BARS, llm=llm, as_of_ts=ts_of(50))
    shown = "\n".join(llm.prompts)

    past = {b["close"] for b in series[:51]}
    future = {b["close"] for b in series[51:]} - past
    leaked = sorted(c for c in future if str(c) in shown)
    assert not leaked, f"the model was shown future prices: {leaked[:5]}"

    assert db.latest_analysis(SYMBOL, "1m")["ts"] == ts_of(50)


# --- the run records how it sampled -------------------------------------

def test_a_run_records_its_stride(series):
    run_id = db.create_replay_run(symbol=SYMBOL, interval="1m",
                                  start_ts=ts_of(30), end_ts=ts_of(190),
                                  status="pending", bars_total=17,
                                  max_analyses=17, stride=10)
    assert db.get_replay_run(run_id)["stride"] == 10


def test_a_run_that_names_no_stride_records_one(series):
    run_id = db.create_replay_run(symbol=SYMBOL, interval="1m",
                                  start_ts=ts_of(30), end_ts=ts_of(190),
                                  status="pending", bars_total=170,
                                  max_analyses=170)
    assert db.get_replay_run(run_id)["stride"] == 1


OLD_REPLAY_RUNS_DDL = """
    CREATE TABLE replay_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL, interval TEXT NOT NULL,
        start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL,
        status TEXT NOT NULL,
        bars_total INTEGER NOT NULL DEFAULT 0,
        bars_done INTEGER NOT NULL DEFAULT 0,
        model TEXT, created_at INTEGER NOT NULL,
        max_analyses INTEGER NOT NULL,
        analyses_done INTEGER NOT NULL DEFAULT 0,
        estimated_tokens INTEGER,
        stop_requested INTEGER NOT NULL DEFAULT 0,
        detail TEXT
    );
"""


def test_a_pre_stride_database_is_migrated(monkeypatch, tmp_path):
    """Existing runs read stride 1, which is what they actually did."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_REPLAY_RUNS_DDL)
    old.execute(
        "INSERT INTO replay_runs (symbol, interval, start_ts, end_ts, status, "
        "created_at, max_analyses) VALUES (?,?,?,?,?,?,?)",
        ("OLD", "1m", 1, 2, "completed", 1, 5))
    old.commit()
    old.close()

    monkeypatch.setenv("DB_PATH", str(path))
    run = db.get_replay_run(1)
    assert run["symbol"] == "OLD"
    assert run["stride"] == 1
