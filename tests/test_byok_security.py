"""Bring-your-own-key: the key must not survive the request.

These tests drive the real OpenAICompatLLM code path (only the outbound
HTTP call is intercepted), so the assertions are about what the actual
key-carrying code does - not about a stub standing in for it.
"""
import itertools
import json
import os
import sqlite3
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_candle_agent_byok.db")
os.environ["LLM_PROVIDER"] = "openai_compat"
os.environ.setdefault("LLM_BASE_URL", "https://api.example.com/v1")
os.environ.setdefault("LLM_MODEL", "test-model")

from fastapi.testclient import TestClient

from candle_agent import config, db, llm as llm_mod, security
from candle_agent.services import api

from .fake_source import ramp

# a realistic, key-shaped secret: long enough to be scrubbed, distinctive
# enough that a substring match cannot be a coincidence
USER_KEY = "sk-user-4f9a2c7e13b64d8fa05e6c1b7d2e9038"
SERVER_KEY = "sk-server-0000111122223333444455556666"
SYMBOL = "KEYTEST"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    def json(self):
        return self._payload


def _completion(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


STAGE1 = json.dumps({
    "regime": "range", "cycle": "compression", "strength": "moderate",
    "key_levels": [100.0, 110.0], "summary": "Balanced two-sided trade.",
})
STAGE2 = json.dumps({
    "decision": "no_trade", "entry": None, "stop": None, "target": None,
    "risk_reward": None, "confidence": "low",
    "reasoning_chain": ["no edge in a balanced range"],
    "decision_path": [
                    {"node": "trend_alignment", "answer": "na", "because": "no trade"},
                    {"node": "level_proximity", "answer": "mid_range", "because": "no trade"},
                    {"node": "stop_placement", "answer": "na", "because": "no trade"},
                    {"node": "risk_reward", "answer": "na", "because": "no trade"}],
})


_counter = itertools.count()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # a fresh database file per test: SQLite in WAL mode keeps handles alive
    # long enough that deleting a shared file races on Windows, and isolated
    # files are better test hygiene anyway
    path = os.path.join(tempfile.gettempdir(), f"byok_{next(_counter)}.db")
    # The counter restarts every session, so these names are reused across
    # runs and the file is only fresh if it is actually removed. A test that
    # asserts a counter is zero would otherwise read yesterday's total.
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    monkeypatch.setenv("DB_PATH", path)
    db.insert_bars(SYMBOL, "1d", ramp(60))

    # the bus double records what would have been published
    published: list[tuple[str, dict]] = []

    class FakeJS:
        async def publish(self, subject, payload):
            published.append((subject, json.loads(payload.decode())))

    class FakeNC:
        """Progress events go core-NATS, not JetStream."""

        async def publish(self, subject, payload):
            published.append((subject, json.loads(payload.decode())))

    # both, and set here rather than inherited: this file used to pass only
    # because another test module happened to populate _state["nc"] first
    api._state["js"] = FakeJS()
    api._state["nc"] = FakeNC()
    monkeypatch.setattr(config, "RATE_LIMIT_PER_HOUR", 0)   # off unless tested
    monkeypatch.setattr(api, "_limiter", security.RateLimiter(0))
    yield published


@pytest.fixture
def upstream(monkeypatch):
    """Intercept only the outbound HTTP call; keep the real client logic."""
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "headers": dict(headers or {}), "json": json})
        system = json["messages"][0]["content"]
        return FakeResponse(_completion(STAGE1 if "STAGE-1" in system else STAGE2))

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    return seen


@pytest.fixture
def client():
    return TestClient(api.app, base_url="https://testserver")


def db_dump() -> str:
    """Every value in every table, as one string."""
    out = []
    with sqlite3.connect(db.db_path()) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            for row in c.execute(f"SELECT * FROM {t}"):
                out.append(" ".join(str(v) for v in row))
    return "\n".join(out)


# --- the core guarantee -------------------------------------------------

def test_user_key_never_reaches_logs_response_db_or_bus(client, upstream, clean, capsys):
    published = clean

    res = client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 200, res.text
    assert res.json()["key_source"] == "user"

    captured = capsys.readouterr()
    assert USER_KEY not in captured.out, "key leaked to stdout"
    assert USER_KEY not in captured.err, "key leaked to stderr"
    assert USER_KEY not in res.text, "key echoed in the response body"
    assert USER_KEY not in db_dump(), "key persisted in the database"
    assert USER_KEY not in json.dumps(published), "key published on the bus"

    # and it really was used upstream, so the test is not vacuous
    assert upstream[0]["headers"]["Authorization"] == f"Bearer {USER_KEY}"


def test_the_analysis_is_still_stored(client, upstream):
    client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    stored = db.latest_analysis(SYMBOL)
    assert stored is not None
    assert stored["stage2"]["decision"] == "no_trade"


def test_user_key_analysis_never_touches_the_request_subject(client, upstream, clean):
    """A key must not ride JetStream, which persists messages to disk."""
    published = clean
    client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    subjects = [s for s, _ in published]
    assert not any(s.startswith("analysis.request.") for s in subjects), subjects
    assert any(s.startswith("analysis.completed.") for s in subjects), subjects


def test_provider_error_echoing_the_key_is_scrubbed(client, monkeypatch):
    def echoing_post(url, headers=None, json=None, timeout=None):
        # providers really do echo credentials back in error bodies
        return FakeResponse({"error": f"invalid api key: {USER_KEY}"}, status_code=401)

    monkeypatch.setattr(llm_mod.httpx, "post", echoing_post)
    res = client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})

    assert res.status_code == 400
    assert USER_KEY not in res.text
    # not even a fragment: providers mask to a tail like "****9038", which
    # no scrubbing regex can recognise, so the body is never forwarded
    for fragment in (USER_KEY[-8:], USER_KEY[-4:], USER_KEY[3:12]):
        assert fragment not in res.text, f"leaked fragment {fragment!r}"
    assert "rejected this API key" in res.json()["detail"]


def test_scrub_removes_key_shaped_tokens_it_was_not_given():
    leaked = "upstream said: sk-live-abcdefghijklmnopqrstuvwxyz is invalid"
    assert "sk-live-abcdefghijklmnopqrstuvwxyz" not in security.scrub(leaked)


# --- fallback and error surface ----------------------------------------

def test_missing_key_everywhere_is_a_400_not_a_500(client, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")

    res = client.post(f"/api/analyze/{SYMBOL}")
    assert res.status_code == 400
    assert "key is required" in res.json()["detail"].lower()


def test_server_key_path_runs_inline_and_never_publishes_the_key(
        client, monkeypatch, clean, upstream):
    """A keyless run is a DEMO run, and it runs here so it can be counted.

    It used to be queued on the bus. The budget may only be charged for an
    analysis that actually happened, and a request handed to the bus is one
    this process never learns the outcome of - so it runs inline, exactly
    as the visitor-key path does, and for the same reason the result rather
    than the request is what reaches JetStream.
    """
    published = clean
    monkeypatch.setattr(config, "LLM_API_KEY", SERVER_KEY)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")

    res = client.post(f"/api/analyze/{SYMBOL}")
    assert res.status_code == 200, res.text
    assert res.json()["key_source"] == "demo"
    # the request never went on the bus; only the finished result did
    assert not any(s.startswith("analysis.request.") for s, _ in published)
    assert any(s.startswith("analysis.completed.") for s, _ in published)
    assert SERVER_KEY not in json.dumps(published)


# --- transport ----------------------------------------------------------

def test_key_over_plain_http_is_refused(upstream, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_INSECURE_KEY_HEADER", False)
    insecure = TestClient(api.app, base_url="http://not-localhost.example")

    res = insecure.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 400
    assert "HTTPS" in res.json()["detail"]


def test_plain_http_is_fine_without_a_key(monkeypatch, clean):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    insecure = TestClient(app=api.app, base_url="http://not-localhost.example")
    assert insecure.post(f"/api/analyze/{SYMBOL}").status_code == 200


def test_loopback_may_send_a_key_over_http(upstream):
    local = TestClient(api.app, base_url="http://localhost")
    res = local.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 200


# --- rate limiting ------------------------------------------------------

def test_analyze_is_rate_limited_by_ip(client, upstream, monkeypatch):
    monkeypatch.setattr(api, "_limiter", security.RateLimiter(2))
    monkeypatch.setattr(config, "RATE_LIMIT_PER_HOUR", 2)

    codes = [client.post(f"/api/analyze/{SYMBOL}",
                         headers={"X-LLM-Key": USER_KEY}).status_code
             for _ in range(3)]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429


def test_rate_limit_applies_even_with_a_user_key(client, upstream, monkeypatch):
    """A visitor key protects their wallet, not this server."""
    monkeypatch.setattr(api, "_limiter", security.RateLimiter(1))
    client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    res = client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 429
    assert res.headers.get("Retry-After")


def test_limiter_windows_out(monkeypatch):
    limiter = security.RateLimiter(1, window_s=0.05)
    assert limiter.check("1.2.3.4")[0] is True
    assert limiter.check("1.2.3.4")[0] is False
    import time
    time.sleep(0.06)
    assert limiter.check("1.2.3.4")[0] is True


def test_forwarded_for_ignored_unless_proxy_is_trusted():
    class Req:
        headers = {"x-forwarded-for": "9.9.9.9"}
        client = type("C", (), {"host": "10.0.0.1"})()

    assert security.client_ip(Req(), trust_proxy=False) == "10.0.0.1"
    assert security.client_ip(Req(), trust_proxy=True) == "9.9.9.9"


# --- key test endpoint --------------------------------------------------

def test_key_test_endpoint_reports_valid(client, upstream):
    res = client.post("/api/llm/test", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 200 and res.json()["valid"] is True


def test_key_test_endpoint_reports_invalid_without_leaking(client, monkeypatch):
    monkeypatch.setattr(llm_mod.httpx, "post", lambda *a, **k: FakeResponse(
        {"error": f"bad key {USER_KEY}"}, status_code=401))

    res = client.post("/api/llm/test", headers={"X-LLM-Key": USER_KEY})
    assert res.status_code == 200
    assert res.json()["valid"] is False
    assert USER_KEY not in res.text


# --- demo budget --------------------------------------------------------
#
# The budget lives in the database, not in RateLimiter, because that class
# says of itself that it "exists to blunt abuse, not to meter billing".
# This meters billing: a restart must not hand out a fresh day.

@pytest.fixture
def demo(monkeypatch):
    """A server key and a real provider - the case that costs money."""
    monkeypatch.setattr(config, "LLM_API_KEY", SERVER_KEY)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "DEMO_DAILY_ANALYSES", 5)
    monkeypatch.setattr(config, "DEMO_PER_IP_ANALYSES", 2)


def test_a_demo_run_is_charged_once_and_only_on_success(client, upstream, demo):
    day = db.utc_day()
    assert db.demo_used(day) == 0

    res = client.post(f"/api/analyze/{SYMBOL}")

    assert res.status_code == 200, res.text
    assert db.demo_used(day) == 1
    assert res.json()["demo"]["remaining"] == 1      # per-IP cap of 2


def test_a_failed_demo_run_costs_the_visitor_nothing(client, monkeypatch, demo):
    """A provider outage is not the visitor's fault and is not their budget."""
    def explode(url, headers=None, json=None, timeout=None):
        return FakeResponse({"error": "upstream on fire"}, status_code=500)

    monkeypatch.setattr(llm_mod.httpx, "post", explode)
    day = db.utc_day()

    res = client.post(f"/api/analyze/{SYMBOL}")

    assert res.status_code == 502
    assert db.demo_used(day) == 0


def test_the_per_ip_cap_stops_one_visitor_draining_the_day(client, upstream, demo):
    for _ in range(2):
        assert client.post(f"/api/analyze/{SYMBOL}").status_code == 200

    res = client.post(f"/api/analyze/{SYMBOL}")

    assert res.status_code == 429
    assert "add your own key" in res.json()["detail"]
    # the day still has budget for somebody else
    assert db.demo_used(db.utc_day()) == 2


def test_the_global_cap_is_not_per_ip(client, upstream, demo):
    """Rotating IPs must not buy more of the day's budget."""
    day = db.utc_day()
    for i in range(5):
        db.record_demo_use(day, f"10.0.0.{i}")       # five different visitors

    res = client.post(f"/api/analyze/{SYMBOL}")

    assert res.status_code == 429
    assert "Daily demo budget used" in res.json()["detail"]


def test_a_visitor_key_is_not_charged_to_the_demo_budget(client, upstream, demo):
    day = db.utc_day()
    for i in range(5):
        db.record_demo_use(day, f"10.0.0.{i}")       # budget fully spent

    res = client.post(f"/api/analyze/{SYMBOL}", headers={"X-LLM-Key": USER_KEY})

    assert res.status_code == 200, res.text
    assert res.json()["key_source"] == "user"
    assert db.demo_used(day) == 5                    # unchanged


def test_the_budget_survives_a_restart(client, upstream, demo):
    client.post(f"/api/analyze/{SYMBOL}")

    # a restart is a fresh process reading the same database
    assert db.demo_used(db.utc_day()) == 1


def test_the_budget_is_keyed_by_utc_day(client, upstream, demo):
    client.post(f"/api/analyze/{SYMBOL}")

    assert db.demo_used(db.utc_day()) == 1
    assert db.demo_used("2099-01-01") == 0           # tomorrow starts at zero


def test_demo_status_reports_what_is_left(client, upstream, demo):
    before = client.get("/api/demo/status").json()
    assert before["metered"] is True
    assert before["remaining"] == 2

    client.post(f"/api/analyze/{SYMBOL}")

    assert client.get("/api/demo/status").json()["remaining"] == 1


def test_a_mock_provider_is_never_metered(client, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(config, "LLM_API_KEY", "")

    assert client.get("/api/demo/status").json()["metered"] is False
    assert client.post(f"/api/analyze/{SYMBOL}").status_code == 200
    assert db.demo_used(db.utc_day()) == 0
