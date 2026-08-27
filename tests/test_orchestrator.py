import os
import tempfile

import pytest

os.environ["LLM_PROVIDER"] = "mock"
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_candle_agent.db")

from candle_agent import db
from candle_agent.demo import synthetic_bars
from candle_agent.orchestrator import analyze
from candle_agent.schemas import consistency_errors


def seed(symbol, n_bars, interval="1m"):
    for b in synthetic_bars(n_bars):
        db.insert_bar(symbol, interval, b["ts"], b["open"], b["high"],
                      b["low"], b["close"], b["volume"])


@pytest.fixture(autouse=True)
def clean_db():
    for suffix in ("", "-wal", "-shm"):     # WAL sidecars hold committed rows
        path = os.environ["DB_PATH"] + suffix
        if os.path.exists(path):
            os.remove(path)
    yield


def test_full_pipeline_with_mock_llm():
    seed("TESTUSD", 60)
    result = analyze("TESTUSD")
    assert result["stage1"]["regime"] in ("bull_trend", "bear_trend", "range", "chop")
    assert result["stage2"]["decision"]
    stored = db.latest_analysis("TESTUSD")
    assert stored is not None and stored["stage2"]["decision"] == result["stage2"]["decision"]


def test_analyze_requires_enough_bars():
    seed("TINY", 5)
    with pytest.raises(RuntimeError):
        analyze("TINY")


def test_consistency_rejects_contradictory_long():
    bad = {"decision": "buy_limit", "entry": 100, "stop": 105, "target": 110,
           "risk_reward": 2.0, "confidence": "high", "reasoning_chain": ["x"]}
    assert consistency_errors(bad)  # stop above entry on a long -> error


def test_consistency_accepts_valid_short():
    good = {"decision": "sell_limit", "entry": 100, "stop": 103, "target": 94,
            "risk_reward": 2.0, "confidence": "medium", "reasoning_chain": ["x"]}
    assert consistency_errors(good) == []
