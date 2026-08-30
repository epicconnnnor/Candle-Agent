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


# Both stage-2 prompts state this floor. The validator used to enforce 1.0,
# so a decision could violate its own playbook and still validate clean.
MIN_RISK_REWARD = 1.5

# Prices are floats, so a setup sitting exactly on the floor lands a hair
# either side of it depending on which subtraction rounds which way - one
# of the six trades in score run 6 cleared 1.5 by 6e-14. Compare with a
# tolerance so the gate expresses the intent rather than the noise.
_RR_EPSILON = 1e-9


def risk_reward(entry, stop, target) -> float | None:
    """Reward-to-risk derived from the geometry, not from the model.

    Stage 2 also *reports* a `risk_reward`, and nothing used to check it:
    a reply claiming 3.0 on geometry worth 1.1 passed every test we had.
    The number that gates a decision has to be the one implied by the
    prices the decision is actually made of.

    None when entry and stop coincide - there is no risk to divide by,
    which is a malformed trade rather than an infinitely good one.
    """
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk == 0:
        return None
    return abs(target - entry) / risk


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
    rr = risk_reward(entry, stop, target)
    if rr is None:
        errs.append("entry and stop are equal, so the trade has no defined risk")
    elif rr < MIN_RISK_REWARD - _RR_EPSILON:
        errs.append(
            f"risk_reward {rr:.3f} is below the {MIN_RISK_REWARD} floor "
            "stated in the playbook; should be no_trade")
    return errs
