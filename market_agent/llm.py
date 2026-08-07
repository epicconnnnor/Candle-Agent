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


class MockLLM:
    """Deterministic offline LLM for demos and tests. Derives price levels
    from the last close in the prompt so downstream consumers (paper
    trader) see realistic, fillable orders."""

    model = "mock"

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
    def __init__(self):
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self.api_key = os.environ["LLM_API_KEY"]  # fail loudly if missing

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
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def get_llm():
    if os.environ.get("LLM_PROVIDER", "mock").lower() == "mock":
        return MockLLM()
    return OpenAICompatLLM()
