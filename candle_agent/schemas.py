"""JSON schemas the LLM output must satisfy. Invalid output -> retry."""
from .features import nearest_level_distance

# The checklist, in the order it must be answered. Fixed and total: every
# decision answers all four, so paths can be compared row for row.
PATH_NODES = ("trend_alignment", "level_proximity", "stop_placement",
              "risk_reward")

# `na` is always allowed - a no_trade has no entry to measure - but it is
# an answer like any other, and claiming it when the geometry exists is
# itself an error.
PATH_ANSWERS = {
    "trend_alignment": ("with_regime", "against", "na"),
    "level_proximity": ("at_level", "mid_range", "na"),
    "stop_placement": ("beyond_swing", "too_tight", "na"),
    "risk_reward": ("pass", "fail", "na"),
}

# The validator's own gate, deliberately NOT scoring's level_proximity_atr.
# That one is a measurement threshold and is meant to be swept; this one is
# a fixed rule the model is held to, and a gate that moves when a sweep
# moves is not a gate.
LEVEL_PROXIMITY_ATR = 0.5

# Below this fraction of ATR a stop is inside the noise it is supposed to
# sit outside of. The playbooks say "roughly 1x ATR14 from entry".
MIN_STOP_ATR = 0.5

STAGE1_SCHEMA = {
    "type": "object",
    "required": ["regime", "cycle", "strength", "key_levels", "summary"],
    "properties": {
        "regime": {"enum": ["bull_trend", "bear_trend", "range", "chop"]},
        # Orthogonal to regime: regime is what shape, cycle is whether the
        # amplitude is expanding and whether it is going anywhere. The four
        # values are the corners of (expanding?, directional?), which is
        # what makes a realized counterpart computable from price alone.
        "cycle": {"enum": ["compression", "breakout", "trend", "exhaustion"]},
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
    "required": ["decision", "confidence", "reasoning_chain", "decision_path"],
    "properties": {
        "decision": {"enum": ["buy_limit", "sell_limit", "buy_stop", "sell_stop", "market_buy", "market_sell", "no_trade"]},
        "entry": {"type": ["number", "null"]},
        "stop": {"type": ["number", "null"]},
        "target": {"type": ["number", "null"]},
        "risk_reward": {"type": ["number", "null"]},
        "confidence": {"enum": ["low", "medium", "high"]},
        # kept alongside decision_path: it is the prose, and dropping it
        # would widen the break from earlier runs for no gain
        "reasoning_chain": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 8,
        },
        # A fixed checklist, not a branching tree. Four nodes, always in
        # this order, always all four - a tree would imply branches nobody
        # has validated, and a variable-length path cannot be aggregated
        # across runs. The nodes are the hard gates the playbooks already
        # state, so three of them can be checked against the geometry.
        "decision_path": {
            "type": "array",
            "minItems": len(PATH_NODES),
            "maxItems": len(PATH_NODES),
            "items": {
                "type": "object",
                "required": ["node", "answer", "because"],
                "properties": {
                    "node": {"enum": list(PATH_NODES)},
                    "answer": {"enum": sorted(
                        {a for answers in PATH_ANSWERS.values() for a in answers})},
                    "because": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
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


TREND_REGIMES = ("bull_trend", "bear_trend")


def stage1_consistency_errors(stage1: dict) -> list[str]:
    """Regime and cycle must describe the same market.

    Stage 1 had no semantic check at all before the cycle field existed -
    a schema-valid diagnosis could say anything. These are the two
    combinations that cannot both be true, not a general coherence
    theory: a trend that is not going anywhere, and chop that is.
    """
    errs = []
    regime, cycle = stage1.get("regime"), stage1.get("cycle")
    if not regime or not cycle:
        return errs
    if regime in TREND_REGIMES and cycle == "compression":
        errs.append(f"regime {regime!r} with cycle 'compression' contradicts "
                    "itself: a trend is directional, compression is not")
    if regime == "chop" and cycle in ("trend", "breakout"):
        errs.append(f"regime 'chop' with cycle {cycle!r} contradicts itself: "
                    "chop means no tradeable structure")
    return errs


def _path_errors(decision: dict, stage1, atr, price) -> list[str]:
    """Cross-check the checklist against the geometry it describes.

    The point of the path is not that the model narrates its reasoning -
    reasoning_chain already did that, unverifiably. It is that three of
    the four answers have a computable truth beside them, so a claim can
    be wrong rather than merely unconvincing.

    stop_placement is the exception and is left declarative: "beyond a
    real swing" needs swing detection this codebase does not have, so
    only the unambiguous half - a stop inside the noise - is checked.
    """
    errs = []
    path = decision.get("decision_path") or []
    answers = {}
    for i, step in enumerate(path):
        node = step.get("node")
        if i < len(PATH_NODES) and node != PATH_NODES[i]:
            errs.append(f"decision_path[{i}] is {node!r}; the checklist is fixed "
                        f"and node {i} must be {PATH_NODES[i]!r}")
        if node in PATH_ANSWERS and step.get("answer") not in PATH_ANSWERS[node]:
            errs.append(f"{node!r} cannot answer {step.get('answer')!r}; allowed: "
                        f"{', '.join(PATH_ANSWERS[node])}")
        answers[node] = step.get("answer")

    d = decision.get("decision")
    entry, stop = decision.get("entry"), decision.get("stop")
    is_trade = d != "no_trade"

    # An answer of "na" is a claim that the question does not apply. On a
    # real trade every one of these applies, so na there is evasion.
    if is_trade:
        for node in ("trend_alignment", "stop_placement", "risk_reward"):
            if answers.get(node) == "na":
                errs.append(f"{node!r} answered 'na' on a {d}: the question applies")

    # level_proximity is the one node that is answerable on EVERY row: a
    # no_trade still has a market price and a diagnosed set of levels to
    # measure it against. Allowing 'na' here would let the whole grader
    # collapse onto the handful of rows that traded, which is the sample
    # problem the checklist exists to escape. Only a diagnosis with no
    # levels at all excuses it.
    if answers.get("level_proximity") == "na" and (stage1 or {}).get("key_levels"):
        errs.append("'level_proximity' answered 'na' but stage 1 named key "
                    "levels: measure from the entry, or from the current "
                    "price when there is no entry")

    regime = (stage1 or {}).get("regime")
    if is_trade and regime in TREND_REGIMES and answers.get("trend_alignment"):
        is_long = d in ("buy_limit", "buy_stop", "market_buy")
        with_regime = is_long == (regime == "bull_trend")
        claimed = answers["trend_alignment"]
        if claimed == "with_regime" and not with_regime:
            errs.append(f"trend_alignment says 'with_regime' but a {d} opposes "
                        f"the diagnosed {regime}")
        if claimed == "against" and with_regime:
            errs.append(f"trend_alignment says 'against' but a {d} follows the "
                        f"diagnosed {regime}")

    # level_proximity is measured from the entry when there is one and from
    # the market otherwise, which is what keeps it answerable - and gradeable
    # - on the no_trade rows that dominate every sample so far.
    reference = entry if entry is not None else price
    levels = (stage1 or {}).get("key_levels")
    claimed = answers.get("level_proximity")
    if reference is not None and atr and levels and claimed in ("at_level", "mid_range"):
        distance = nearest_level_distance(reference, atr, levels)
        if distance is not None:
            at_level = distance <= LEVEL_PROXIMITY_ATR
            if claimed == "at_level" and not at_level:
                errs.append(f"level_proximity says 'at_level' but the nearest "
                            f"key level is {distance:.2f} ATR away")
            if claimed == "mid_range" and at_level:
                errs.append(f"level_proximity says 'mid_range' but a key level "
                            f"is {distance:.2f} ATR away")

    if is_trade and atr and entry is not None and stop is not None:
        if answers.get("stop_placement") == "beyond_swing":
            if abs(entry - stop) < MIN_STOP_ATR * atr:
                errs.append(
                    f"stop_placement says 'beyond_swing' but the stop is "
                    f"{abs(entry - stop) / atr:.2f} ATR from entry, inside the "
                    "noise it is meant to sit outside of")

    rr = risk_reward(decision.get("entry"), stop, decision.get("target"))
    claimed = answers.get("risk_reward")
    if rr is not None and claimed in ("pass", "fail"):
        passes = rr >= MIN_RISK_REWARD - _RR_EPSILON
        if claimed == "pass" and not passes:
            errs.append(f"risk_reward says 'pass' but the geometry gives {rr:.3f}, "
                        f"below the {MIN_RISK_REWARD} floor")
        if claimed == "fail" and passes:
            errs.append(f"risk_reward says 'fail' but the geometry gives {rr:.3f}")
    return errs


def consistency_errors(decision: dict, stage1: dict | None = None,
                       atr: float | None = None,
                       price: float | None = None) -> list[str]:
    """Signal-chain checks beyond the schema: catch 'says bullish, JSON does short'
    style contradictions.

    `stage1`, `atr` and `price` are the context the checklist is checked
    against. They default to None so the geometry checks still run on
    their own - but without them the decision_path can only be checked
    for shape, not for truth.
    """
    errs = _path_errors(decision, stage1, atr, price)
    d = decision.get("decision")
    if d == "no_trade":
        return errs      # path errors above still stand
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
