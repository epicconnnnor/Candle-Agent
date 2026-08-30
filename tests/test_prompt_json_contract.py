"""The json_object contract.

Providers that implement `response_format: {"type": "json_object"}` reject
any request whose messages do not contain the word "json" - DeepSeek
answers HTTP 400. That failure only shows up against the live API, so the
invariant is pinned here instead:

    response_format is set  =>  the messages contain "json"

Both halves are asserted from the actual outbound request body, not from
reading the source, so a future edit to either the prompts or the client
breaks the test rather than production.

The rule is scoped to the ANALYSIS prompts (stage*.txt), because they are
the ones complete() sends with response_format. The follow-up chat prompt
goes through converse(), which sets no response_format at all - so it is
held to the other half of the implication instead: no json mode, no
obligation to mention json.

The word is required in LOWERCASE. OpenAI's implementation of this check
is case-insensitive, but DeepSeek's is not documented as such, and a
lowercase occurrence satisfies either reading.
"""
import json
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_prompt_contract.db")
os.environ["LLM_BASE_URL"] = "https://api.example.com/v1"
os.environ["LLM_MODEL"] = "test-model"

from candle_agent import chat
from candle_agent import llm as llm_mod
from candle_agent.llm import OpenAICompatLLM
from candle_agent.orchestrator import PROMPTS, ROUTES

KEY = "sk-contract-000011112222333344445555"


@pytest.fixture
def sent(monkeypatch):
    """Capture outbound request bodies instead of making them."""
    bodies = []

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"total_tokens": 1}}

    def fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(json)
        return Resp()

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    return bodies


# --- the prompts themselves --------------------------------------------

def test_every_analysis_prompt_contains_lowercase_json():
    missing = [
        f.name for f in sorted(PROMPTS.glob("stage*.txt"))
        if "json" not in f.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"{missing} lack a lowercase 'json'. They are sent with "
        "response_format=json_object, which providers reject unless the "
        "word appears in the messages.")


def test_all_stage2_routes_are_covered():
    """Every regime routes to a prompt that exists and satisfies the rule."""
    for regime, filename in ROUTES.items():
        path = PROMPTS / filename
        assert path.exists(), f"{regime} routes to a missing prompt {filename}"
        assert "json" in path.read_text(encoding="utf-8"), regime


# --- the client's outbound requests ------------------------------------

def test_complete_sets_json_object_and_says_json(sent):
    llm = OpenAICompatLLM(api_key=KEY)
    system = (PROMPTS / "stage1_diagnose.txt").read_text(encoding="utf-8")
    llm.complete(system, "symbol: TEST\nlast close: 100.0")

    body = sent[0]
    assert body["response_format"] == {"type": "json_object"}
    messages = json.dumps(body["messages"])
    assert "json" in messages, "json_object mode without the word 'json'"


def test_ping_sends_no_response_format(sent):
    """The credential check must not inherit the json constraint.

    This is the regression: ping used to reuse complete(), so a minimal
    "are these credentials valid" call carried response_format and was
    rejected 400 for not mentioning json.
    """
    OpenAICompatLLM(api_key=KEY).ping()

    body = sent[0]
    assert "response_format" not in body
    assert body["max_tokens"] == 1
    assert "json" not in json.dumps(body["messages"]).lower()


def test_the_invariant_holds_for_every_request_the_client_makes(sent):
    """response_format set => messages mention json. Checked over both paths."""
    llm = OpenAICompatLLM(api_key=KEY)
    llm.ping()
    for name in sorted(p.name for p in PROMPTS.glob("stage*.txt")):
        llm.complete((PROMPTS / name).read_text(encoding="utf-8"), "last close: 100.0")
    # the follow-up path, which must never carry response_format
    llm.converse([{"role": "system", "content": chat.system_prompt()},
                  {"role": "user", "content": "why no trade?"}])

    assert len(sent) == 2 + len(list(PROMPTS.glob("stage*.txt")))
    for body in sent:
        if body.get("response_format", {}).get("type") == "json_object":
            assert "json" in json.dumps(body["messages"]), body["messages"][0]["content"][:80]


def test_converse_sets_no_response_format(sent):
    """The other half of the implication.

    A follow-up answer is prose, so it must not request json mode - and
    because it does not, the chat prompt is free of the obligation to say
    "json", which would otherwise be a stray word in a plain-English
    instruction.
    """
    llm = OpenAICompatLLM(api_key=KEY)
    llm.converse([{"role": "system", "content": chat.system_prompt()},
                  {"role": "user", "content": "explain the levels"}])

    body = sent[0]
    assert "response_format" not in body
    assert "json" not in chat.system_prompt()


# --- analysis provenance columns ----------------------------------------

def test_price_at_and_atr_at_round_trip(tmp_path, monkeypatch):
    """A new analysis records the market it was formed against."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "provenance.db"))
    from candle_agent import db

    db.insert_analysis("AAPL", 1, {"regime": "range"}, {"decision": "no_trade"},
                       "deepseek-chat", 900, interval="1m",
                       price_at=314.54, atr_at=0.62)
    row = db.latest_analysis("AAPL", "1m")
    assert row["price_at"] == 314.54
    assert row["atr_at"] == 0.62


def test_pre_migration_rows_read_null_not_a_fabricated_price(tmp_path, monkeypatch):
    """The UI shows these as 'age unknown'; a DEFAULT would fake freshness."""
    import sqlite3
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("DB_PATH", str(path))

    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL, ts INTEGER NOT NULL, stage1 TEXT NOT NULL,
          stage2 TEXT NOT NULL, model TEXT, latency_ms INTEGER,
          interval TEXT NOT NULL DEFAULT '1m');
    """)
    c.execute("INSERT INTO analyses (symbol, ts, stage1, stage2, model, latency_ms, interval)"
              " VALUES (?,?,?,?,?,?,?)",
              ("AAPL", 1, '{"regime":"range"}', '{"decision":"no_trade"}', "old", 5, "1m"))
    c.commit()
    c.close()

    from candle_agent import db
    row = db.latest_analysis("AAPL")
    assert row["price_at"] is None
    assert row["atr_at"] is None


def test_latest_analysis_is_scoped_by_interval(tmp_path, monkeypatch):
    """Restoring across intervals is what put wrong levels on the chart."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "scoped.db"))
    from candle_agent import db

    db.insert_analysis("AAPL", 1, {"regime": "range"}, {"decision": "no_trade"},
                       "m", 1, interval="1m", price_at=100.0, atr_at=1.0)
    db.insert_analysis("AAPL", 2, {"regime": "bull_trend"}, {"decision": "buy_limit"},
                       "m", 1, interval="5m", price_at=200.0, atr_at=2.0)

    assert db.latest_analysis("AAPL", "1m")["price_at"] == 100.0
    assert db.latest_analysis("AAPL", "5m")["price_at"] == 200.0
    assert db.latest_analysis("AAPL", "1h") is None


# --- fingerprint coverage ----------------------------------------------
#
# This hash has been incomplete twice: once it globbed every *.txt and swept
# in a prompt outside the contract, once it hashed one of three validator
# gates. Neither was visible by reading it - an omission looks exactly like
# a hash that has not changed. These tests fail instead.

def _schema_constants():
    """Module-level constants in schemas.py, discovered HERE.

    Deliberately not orchestrator.validator_gates(): a test that asks the
    code under test what it ought to cover cannot catch the code covering
    too little. Narrowing validator_gates() narrowed this list with it,
    and an earlier version of this test passed against a fingerprint that
    had been made incomplete on purpose.
    """
    import types as _types

    from candle_agent import schemas

    return sorted(
        name for name, value in vars(schemas).items()
        if not name.startswith("__")
        and name.lstrip("_").isupper()
        and not callable(value)
        and not isinstance(value, _types.ModuleType)
    )


def test_every_validator_constant_moves_the_fingerprint(monkeypatch):
    """Adding a gate without covering it must break the build, not a sample."""
    from candle_agent import orchestrator, schemas

    names = _schema_constants()
    assert names, "no validator constants discovered at all"

    base = orchestrator.prompt_fingerprint()
    for name in names:
        monkeypatch.setattr(schemas, name, "MUTATED-FOR-TEST", raising=True)
        assert orchestrator.prompt_fingerprint() != base, (
            f"{name} is a module-level constant in schemas.py but changing it "
            "does not change the fingerprint, so a sample could pool rows "
            "validated under two different versions of it")
        monkeypatch.undo()


def test_a_new_validator_constant_is_covered_the_moment_it_exists(monkeypatch):
    """The enumeration is the guarantee - not a hand-maintained list."""
    from candle_agent import orchestrator, schemas

    base = orchestrator.prompt_fingerprint()
    monkeypatch.setattr(schemas, "MAX_SPREAD_ATR", 0.25, raising=False)

    assert "MAX_SPREAD_ATR" in dict(orchestrator.validator_gates())
    assert orchestrator.prompt_fingerprint() != base


def test_every_prompt_is_either_contract_or_deliberately_excluded():
    """A new prompt file cannot be silently outside the contract."""
    from candle_agent.orchestrator import (NON_CONTRACT_PROMPTS, PROMPTS,
                                           contract_prompts)

    on_disk = {p.name for p in PROMPTS.glob("*.txt")}
    covered = {p.name for p in contract_prompts()}
    uncovered = on_disk - covered

    assert uncovered == set(NON_CONTRACT_PROMPTS), (
        f"{uncovered - set(NON_CONTRACT_PROMPTS)} are in the prompts directory "
        "but neither fingerprinted nor listed in NON_CONTRACT_PROMPTS. Decide "
        "which: a contract prompt must move the fingerprint.")
    assert covered, "no contract prompts found"


def test_editing_any_contract_prompt_moves_the_fingerprint():
    """Enumerated from disk, not from contract_prompts(), for the same
    reason the constants are: the code under test does not get to say
    which files it is responsible for."""
    from candle_agent import orchestrator
    from candle_agent.orchestrator import NON_CONTRACT_PROMPTS, PROMPTS

    base = orchestrator.prompt_fingerprint()
    for path in sorted(PROMPTS.glob("*.txt")):
        if path.name in NON_CONTRACT_PROMPTS:
            continue
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n# edited by a test\n")
            assert orchestrator.prompt_fingerprint() != base, (
                f"editing {path.name} did not move the fingerprint")
        finally:
            path.write_bytes(original)
    assert orchestrator.prompt_fingerprint() == base


def test_the_chat_prompt_does_not_move_the_analysis_contract():
    """It changes nothing about what stage 1 or stage 2 is asked."""
    from candle_agent.orchestrator import PROMPTS, prompt_fingerprint

    path = PROMPTS / "followup_chat.txt"
    base = prompt_fingerprint()
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\nAnswer briefly.\n")
        assert prompt_fingerprint() == base
    finally:
        path.write_bytes(original)


def test_the_fingerprint_is_stable_across_calls():
    """Unordered constants must not leak set ordering into the hash."""
    from candle_agent.orchestrator import prompt_fingerprint

    assert len({prompt_fingerprint() for _ in range(5)}) == 1
