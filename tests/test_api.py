"""API tests for /symbols and /subscribe.

The bus is stubbed with a request/reply double, so these run without NATS
and without an ingest process.
"""
import json
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_candle_agent_api.db")

from fastapi.testclient import TestClient

from candle_agent import db, sources, symbols
from candle_agent.services import api

from .fake_source import FakeSource, ramp

SYMBOL = "FAKEUSDT"


class FakeReply:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()


class FakeNC:
    """Answers ingest.control.subscribe the way the ingest service would."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.requests = []

    async def request(self, subject, payload, timeout=None):
        self.requests.append((subject, json.loads(payload.decode())))
        if self.error:
            raise self.error
        req = self.requests[-1][1]
        return FakeReply(self.reply or {
            "status": "ok", "changed": True, "source": req["source"],
            "symbol": req["symbol"], "interval": req["interval"],
            "mode": "live", "state": {},
        })


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    for suffix in ("", "-wal", "-shm"):
        path = os.environ["DB_PATH"] + suffix
        if os.path.exists(path):
            os.remove(path)
    sources.reset({"fake": FakeSource(symbol=SYMBOL)})
    symbols.reset()
    nc = FakeNC()
    api._state["nc"] = nc
    yield nc
    sources.reset(None)
    symbols.reset()


@pytest.fixture
def client():
    # no context manager: the lifespan would try to reach a real NATS
    return TestClient(api.app)


# --- /symbols ----------------------------------------------------------

def test_symbols_returns_the_merged_catalogue(client):
    r = client.get("/symbols")
    assert r.status_code == 200

    body = r.json()
    assert body["sources"] == ["fake"]
    assert "1m" in body["intervals"] and "1d" in body["intervals"]
    assert body["unavailable"] == {}

    entry = body["symbols"][0]
    assert entry["symbol"] == SYMBOL
    assert entry["source"] == "fake"            # every entry names its source
    assert entry["asset_class"] == "crypto"
    assert entry["name"]


def test_symbols_can_be_filtered_by_source(client):
    assert client.get("/symbols", params={"source": "fake"}).json()["symbols"]
    assert client.get("/symbols", params={"source": "binance"}).json()["symbols"] == []


def test_symbols_is_cached(client, monkeypatch):
    source = sources.get("fake")
    calls = {"n": 0}
    original = source.list_symbols

    async def counted():
        calls["n"] += 1
        return await original()

    monkeypatch.setattr(source, "list_symbols", counted)
    client.get("/symbols")
    client.get("/symbols")
    assert calls["n"] == 1                      # second request served from cache


def test_symbols_503_when_no_source_can_answer(client):
    from candle_agent.sources.base import RegionBlocked

    broken = FakeSource(symbol=SYMBOL)

    async def blocked():
        raise RegionBlocked("Binance refused ... (HTTP 451).", code=451)

    broken.list_symbols = blocked
    sources.reset({"fake": broken})
    symbols.reset()

    r = client.get("/symbols")
    assert r.status_code == 503
    assert "451" in r.json()["detail"]


# --- /subscribe --------------------------------------------------------

def test_subscribe_routes_to_ingest_and_returns_bars(client, stub):
    db.insert_bars(SYMBOL, "5m", ramp(10))

    r = client.post("/subscribe", json={"symbol": SYMBOL, "interval": "5m"})
    assert r.status_code == 200

    body = r.json()
    assert body["status"] == "ok"
    assert body["source"] == "fake"             # inferred from the catalogue
    assert body["interval"] == "5m"
    assert body["asset_class"] == "crypto"
    assert len(body["bars"]) == 10              # chart can render immediately

    subject, sent = stub.requests[0]
    assert subject == "ingest.control.subscribe"
    assert sent == {"symbol": SYMBOL, "interval": "5m", "source": "fake"}


def test_subscribe_returns_only_the_requested_interval(client):
    db.insert_bars(SYMBOL, "1m", ramp(4))
    db.insert_bars(SYMBOL, "5m", ramp(7))

    body = client.post("/subscribe", json={"symbol": SYMBOL, "interval": "1m"}).json()
    assert len(body["bars"]) == 4               # not a mix of granularities


def test_unknown_symbol_is_a_400(client):
    r = client.post("/subscribe", json={"symbol": "NOPEUSDT", "interval": "1m"})
    assert r.status_code == 400
    assert "NOPEUSDT" in r.json()["detail"]


def test_es_and_nq_are_no_longer_accepted(client):
    for symbol in ("ES", "NQ"):
        assert client.post("/subscribe", json={"symbol": symbol}).status_code == 400


def test_unsupported_interval_is_a_400(client):
    r = client.post("/subscribe", json={"symbol": SYMBOL, "interval": "7m"})
    assert r.status_code == 400
    assert "7m" in r.json()["detail"]


def test_unknown_source_is_a_400(client):
    r = client.post("/subscribe",
                    json={"symbol": SYMBOL, "interval": "1m", "source": "kraken"})
    assert r.status_code == 400
    assert "kraken" in r.json()["detail"]


def test_symbol_is_normalized(client, stub):
    client.post("/subscribe", json={"symbol": "  fakeusdt  ", "interval": "1m"})
    assert stub.requests[0][1]["symbol"] == SYMBOL


def test_ingest_not_responding_is_a_503(client, monkeypatch):
    api._state["nc"] = FakeNC(error=TimeoutError())
    r = client.post("/subscribe", json={"symbol": SYMBOL, "interval": "1m"})
    assert r.status_code == 503
    assert "ingest" in r.json()["detail"]


def test_ingest_rejection_is_surfaced(client):
    api._state["nc"] = FakeNC(reply={"status": "error", "message": "interval not supported"})
    r = client.post("/subscribe", json={"symbol": SYMBOL, "interval": "1m"})
    assert r.status_code == 400
    assert "interval not supported" in r.json()["detail"]
