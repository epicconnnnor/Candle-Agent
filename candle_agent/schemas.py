"""JSON schemas the LLM output must satisfy. Invalid output -> retry."""

STAGE1_SCHEMA = {
    "type": "object",
    "required": ["regime", "strength", "key_levels", "summary"],
    "properties": {
        "regime": {"enum": ["bull_trend", "bear_trend", "range", "chop"]},
        "strength": {"enum": ["weak", "moderate", "strong"]},
        "key_levels": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 0,
            "maxItems": 6,
        },
        "summary": {"type": "string", "maxLength": 400},
    },
    "additionalProperties": False,
}

STAGE2_SCHEMA = {
    "type": "object",
    "required": ["decision", "confidence", "reasoning_chain"],
    "properties": {
        "decision": {"enum": ["buy_limit", "sell_limit", "buy_stop", "sell_stop", "market_buy", "market_sell", "no_trade"]},
        "entry": {"type": ["number", "null"]},
        "stop": {"type": ["number", "null"]},
        "target": {"type": ["number", "null"]},
        "risk_reward": {"type": ["number", "null"]},
        "confidence": {"enum": ["low", "medium", "high"]},
        "reasoning_chain": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 8,
        },
    },
    "additionalProperties": False,
}


def consistency_errors(decision: dict) -> list[str]:
    """Signal-chain checks beyond the schema: catch 'says bullish, JSON does short'
    style contradictions."""
    errs = []
    d = decision.get("decision")
    if d == "no_trade":
        return errs
    entry, stop, target = decision.get("entry"), decision.get("stop"), decision.get("target")
    if entry is None or stop is None or target is None:
        return ["trade decision must include entry, stop and target"]
    is_long = d in ("buy_limit", "buy_stop", "market_buy")
    if is_long and not (stop < entry < target):
        errs.append("long trade requires stop < entry < target")
    if not is_long and not (target < entry < stop):
        errs.append("short trade requires target < entry < stop")
    rr = decision.get("risk_reward")
    if rr is not None and rr < 1.0:
        errs.append("risk_reward below 1.0 should be no_trade")
    return errs
