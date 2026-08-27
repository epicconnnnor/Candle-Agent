"""LLM client. Any OpenAI-compatible API works (DeepSeek, OpenRouter, Groq,
OpenAI, ...). Provider is pure config:

    LLM_BASE_URL=https://api.deepseek.com/v1
    LLM_MODEL=deepseek-chat
    LLM_API_KEY=sk-...

Set LLM_PROVIDER=mock for a deterministic offline mock (demo/tests).
"""
import json
import os

import httpx

from .security import scrub


class LLMKeyRequired(RuntimeError):
    """No key at all: neither the visitor nor the server supplied one."""


class LLMAuthFailed(RuntimeError):
    """The upstream provider rejected the key."""


class LLMUpstreamError(RuntimeError):
    """The provider failed for a reason unrelated to the key."""


class MockLLM:
    """Deterministic offline LLM for demos and tests. Derives price levels
    from the last close in the prompt so downstream consumers (paper
    trader) see realistic, fillable orders."""

    model = "mock"

    def __init__(self):
        self.usage: list[dict] = []     # same attribute as the real client

    def ping(self) -> str:
        return "ok"

    def complete(self, system: str, user: str) -> str:
        import re
        m = re.search(r'last close:\s*([0-9.]+)', user)
        close = float(m.group(1)) if m else 100.0
        if "STAGE-1" in system:
            return json.dumps(
                {
                    "regime": "bull_trend",
                    "strength": "moderate",
                    "key_levels": [round(close * 0.99, 2), round(close * 1.02, 2)],
                    "summary": "Higher lows, price holding above EMA20 (mock diagnosis).",
                }
            )
        entry = round(close * 0.998, 2)          # shallow pullback limit
        stop = round(entry - close * 0.008, 2)   # ~0.8% risk
        target = round(entry + 2 * (entry - stop), 2)  # 2R
        return json.dumps(
            {
                "decision": "buy_limit",
                "entry": entry,
                "stop": stop,
                "target": target,
                "risk_reward": 2.0,
                "confidence": "medium",
                "reasoning_chain": [
                    "pullback toward EMA20 in bull trend",
                    "prior breakout held (mock reasoning)",
                ],
            }
        )


class OpenAICompatLLM:
    def __init__(self, api_key: str | None = None):
        """`api_key` overrides the server key for this instance only.

        A visitor's key arrives per request and lives on one short-lived
        client object; nothing here writes it down.
        """
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        if not self.api_key:
            raise LLMKeyRequired(
                "No LLM API key available. Supply one in Settings, or configure "
                "LLM_API_KEY on the server.")
        # one entry per completion, so a caller can price a whole analysis
        self.usage: list[dict] = []

    def _raise_for_status(self, resp) -> None:
        if resp.status_code in (401, 403):
            # The provider body is deliberately NOT forwarded. Bodies echo
            # the key back - DeepSeek returns a masked "****ghij" tail, and
            # others return it whole. For a rejected key the body carries no
            # information beyond "rejected", so dropping it costs nothing
            # and removes the last route by which any part of a key escapes.
            raise LLMAuthFailed(
                f"The LLM provider rejected this API key (HTTP {resp.status_code}). "
                "Check the key and that it has credit.")
        if resp.status_code >= 400:
            raise LLMUpstreamError(scrub(
                f"The LLM provider returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}", self.api_key))

    def ping(self) -> str:
        """Smallest possible credential check.

        Deliberately omits response_format. Providers that implement
        json_object mode reject any request whose messages do not contain
        the word "json" - DeepSeek answers HTTP 400 - and a credential
        check has no business carrying that constraint. max_tokens=1 keeps
        the cost at effectively nothing.
        """
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=30,
        )
        self._raise_for_status(resp)
        return "ok"

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60,
        )

        self._raise_for_status(resp)
        body = resp.json()
        # providers report usage per call; keep it rather than re-deriving
        # token counts with a tokenizer we do not have
        u = body.get("usage") or {}
        self.usage.append({
            "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "total_tokens": u.get("total_tokens"),
            # DeepSeek reports cache hits/misses; they price differently
            "cache_hit_tokens": u.get("prompt_cache_hit_tokens"),
            "cache_miss_tokens": u.get("prompt_cache_miss_tokens"),
        })
        return body["choices"][0]["message"]["content"]


def get_llm(api_key: str | None = None):
    """The configured client, or a real one when a visitor brings a key.

    A supplied key always means a real provider: someone who pastes their
    own credentials wants a real model, not the offline mock.
    """
    if api_key:
        return OpenAICompatLLM(api_key=api_key)
    if os.environ.get("LLM_PROVIDER", "mock").lower() == "mock":
        return MockLLM()
    return OpenAICompatLLM()
