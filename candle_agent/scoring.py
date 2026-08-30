"""Walk-forward scoring of stored analyses. Pure functions over dicts - no
I/O - so the same logic runs from the API, from a test, or from a notebook.

For every analysis: what actually happened next? Three graders read the
same forward window, and they answer different questions because the
sample sizes are wildly different.

  entries      did target or stop come first
  abstentions  was declining right - did a payable move exist at all
  diagnosis    was "range" actually a range

Only the third has a usable sample early on: 25 analyses of which 23 are
no_trade give you 2 trade outcomes but 25 regime verdicts.

Two rules the whole module obeys.

STORE MEASURES, DERIVE LABELS. Every threshold below is a judgement call.
The continuous quantity that a threshold is applied to is always kept, so
a threshold can be re-swept later without re-running anything and without
a single LLM call. Never store only the label.

NOTHING HERE MAY LOOK BACKWARDS. Scoring reads only bars strictly after
the analysis, and takes its anchor from the analysis's own stored
price_at/atr_at rather than recomputing from the table - so there is no
code path where the scorer's window can drift from the one the analysis
was actually formed against. It writes only to score tables, which no
prompt ever reads. tests/test_scoring.py holds it to that, vacuity check
included.
"""
from . import paper
from .features import atr as atr_series, envelope_atr, nearest_level_distance
from .schemas import PATH_NODES, consistency_errors

SCORER_VERSION = "2"

TREND_REGIMES = ("bull_trend", "bear_trend")
FLAT_REGIMES = ("range", "chop")

# Every arbitrary number in the scoring layer, in one place, recorded with
# every score run. See docs/scoring-design.md for what each one is worth
# and which way it biases the result.
DEFAULTS = {
    # The forward window. 30 because that is what the analyzer itself
    # looks back over (MIN_BARS, and n_recent in build_feature_packet): a
    # verdict formed on 30 bars of structure is judged on 30 bars of
    # consequence. Symmetry is not proof, but every alternative is a round
    # number or one tuned until the results looked good.
    "horizon_bars": 30,
    # Inherited from paper.PENDING_TTL_BARS so there is one fill TTL in
    # the codebase rather than two that can drift apart.
    "fill_window_bars": paper.PENDING_TTL_BARS,
    # The abstention grader runs on its own, SHORTER horizon, and the two
    # are not cross-tabulable - see summarize(), which states both.
    #
    # The barriers below are an ATR multiple, and ATR14 on 1m bars is a
    # BAR-scale quantity. Using it as a WINDOW-scale barrier is a units
    # error: over 30 bars price routinely travels several multiples of a
    # single bar's range, so a 1.5/1.0 test fires on 80% of bars (measured:
    # 0.803 over 390 clean AAPL 1m bars) and cannot tell a good no_trade
    # from a bad one. The rate scales as sqrt(horizon), so the fix is
    # either a shorter window or bigger barriers; this is the shorter
    # window, which stays closer to the geometry the prompt actually
    # states. 10 bars at 3.0/2.0 measures 0.337. Re-derive with
    # sweep_baselines() on any new instrument or interval - these numbers
    # are calibrated to 1m equities, not universal.
    "abstention_horizon_bars": 10,
    "target_atr": 3.0,
    "stop_atr": 2.0,
    # The most arbitrary number in the file. 0.3-0.4 is the conventional
    # "trending" band for the Kaufman efficiency ratio; convention is not
    # derivation. fwd_efficiency is stored raw so this can be swept.
    "trend_efficiency": 0.35,
    "trend_displacement_atr": 1.5,
    # Range vs chop is an amplitude question - the prompts define chop as
    # having "no tradeable structure". 2.5 = a 1.5 target plus a 1.0 stop,
    # i.e. the envelope is a range if the strategy's own trade fits in it.
    "range_envelope_atr": 2.5,
    # Only used to record how far a decision sat from its own diagnosed
    # key levels. The level grader itself is deliberately not built yet.
    "level_proximity_atr": 0.5,
    # The cycle grader's amplitude band. A = forward envelope / the
    # envelope stored on the analysis, so A >= k is expansion and A <= 1/k
    # is contraction; between them is "steady".
    #
    # 1.10 was swept on MSFT 1m - a DIFFERENT instrument from the AAPL
    # series it will be used to score, which is the one thing run 6's
    # abstention barriers got wrong. Across 283 windows it minimises the
    # majority-class baseline (0.569 at k=1.10, rising monotonically to
    # 0.866 at k=2.5), which is the only property worth optimising: a k
    # that lets one label swallow 90% of windows produces a baseline
    # nothing can beat.
    #
    # Known limitation, recorded rather than tuned away: only ~11% of 1m
    # windows are directional at trend_efficiency 0.35, so `trend` and
    # `breakout` split a small population and `trend` is nearly
    # unreachable at this k (1 of 283). On 1m equities in a rangebound
    # week this is effectively a three-class grader. Re-sweep per
    # instrument and interval; fwd_envelope_ratio is stored raw so it
    # costs no LLM call.
    "cycle_amplitude_k": 1.10,
    "secondary_horizons": (10, 60),
}


class ScoringError(ValueError):
    """A caller mistake - bad parameters - not a data problem."""


def resolve_params(overrides: dict | None = None) -> dict:
    """Merge overrides over the defaults, rejecting anything unrecognised.

    A silently ignored override would produce scores that disagree with
    their own recorded parameters, which is worse than an error.
    """
    params = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            raise ScoringError(
                f"unknown scoring parameter {key!r}; known parameters are "
                f"{', '.join(sorted(DEFAULTS))}")
        params[key] = value

    if int(params["horizon_bars"]) < 1:
        raise ScoringError("horizon_bars must be at least 1")
    if int(params["abstention_horizon_bars"]) < 1:
        raise ScoringError("abstention_horizon_bars must be at least 1")
    if float(params["target_atr"]) <= 0 or float(params["stop_atr"]) <= 0:
        raise ScoringError("target_atr and stop_atr must be greater than zero")
    # Raising this above the shared TTL would do nothing - paper.on_bar
    # expires the order first - so refuse instead of lying about it.
    if int(params["fill_window_bars"]) > paper.PENDING_TTL_BARS:
        raise ScoringError(
            f"fill_window_bars cannot exceed paper.PENDING_TTL_BARS "
            f"({paper.PENDING_TTL_BARS}): the shared fill logic expires a "
            "pending order at that point, so a longer window would have no "
            "effect on the result")
    return params


# --- forward market facts, shared by all three graders ------------------

def forward_measures(anchor: float, atr: float, bars: list[dict]) -> dict:
    """Everything the graders need about what happened next.

    `bars` are the forward bars only. Everything is in ATR-at-decision
    units, which is what makes rows comparable across symbols, price
    levels and volatility.

    Excursions are measured against the anchor and are NOT clamped at
    zero. `fwd_mfe_atr` goes negative when the window's high never trades
    back up to the anchor - a gap down, or an immediate decline - and
    `fwd_mae_atr` goes positive in the mirror case. The invariant that
    actually holds is `fwd_mfe_atr >= fwd_mae_atr`, and that
    mae <= return <= mfe; neither excursion is bounded by zero. An
    earlier version of this docstring claimed otherwise and was wrong.
    """
    if not bars or not atr or atr <= 0:
        return {"fwd_mfe_atr": None, "fwd_mae_atr": None, "fwd_return_atr": None,
                "fwd_efficiency": None, "fwd_envelope_atr": None}

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    # The path starts at the decision close, so the first step is the move
    # into the first forward bar rather than between two forward bars.
    path = [anchor] + [b["close"] for b in bars]
    travelled = sum(abs(b - a) for a, b in zip(path, path[1:]))

    return {
        "fwd_mfe_atr": round((max(highs) - anchor) / atr, 4),
        "fwd_mae_atr": round((min(lows) - anchor) / atr, 4),
        "fwd_return_atr": round((path[-1] - anchor) / atr, 4),
        "fwd_efficiency": round(abs(path[-1] - path[0]) / travelled, 4) if travelled else 0.0,
        "fwd_envelope_atr": round((max(highs) - min(lows)) / atr, 4),
    }


def contiguous_prefix(bars: list[dict], interval_ms: int | None) -> list[dict]:
    """Bars up to the first gap, exclusive of everything after it.

    A forward window has to be N CONSECUTIVE bars to mean anything. The
    bars table spans session breaks, and when a symbol has been reused for
    demo data it spans outright instrument seams - a plain `LIMIT 30`
    reads straight across both. An overnight gap inside a 1m window makes
    every barrier trivially reachable and every regime look like a trend,
    which silently inflates the base rate rather than raising an error.
    """
    if not bars or not interval_ms:
        return list(bars)
    out = [bars[0]]
    for prev, cur in zip(bars, bars[1:]):
        if cur["ts"] - prev["ts"] != interval_ms:
            break
        out.append(cur)
    return out


def classify_regime(measures: dict, params: dict) -> str | None:
    """The realized regime, from price alone. No model output involved."""
    displacement = measures.get("fwd_return_atr")
    efficiency = measures.get("fwd_efficiency")
    envelope = measures.get("fwd_envelope_atr")
    if displacement is None or efficiency is None or envelope is None:
        return None

    if (efficiency >= params["trend_efficiency"]
            and abs(displacement) >= params["trend_displacement_atr"]):
        return "bull_trend" if displacement > 0 else "bear_trend"
    if envelope >= params["range_envelope_atr"]:
        return "range"
    return "chop"


# The four cycle labels are the corners of two binary facts, which is what
# makes every one of them computable from price: is the amplitude
# EXPANDING, and is price GOING ANYWHERE.
CYCLE_EXPANDING = {"breakout": True, "exhaustion": True,
                   "trend": False, "compression": False}
CYCLE_DIRECTIONAL = {"breakout": True, "trend": True,
                     "exhaustion": False, "compression": False}


def classify_cycle(measures: dict, envelope_at, params: dict) -> str | None:
    """The realized cycle phase, from price alone. No model output involved.

    `envelope_at` is the amplitude of the window the analysis was shown,
    stored on the analysis row. It is NOT recomputed here: the scorer
    reads only bars after the analysis, and a backward read would be free
    to drift from the window the verdict was formed against.
    """
    fwd = measures.get("fwd_envelope_atr")
    efficiency = measures.get("fwd_efficiency")
    if fwd is None or efficiency is None or not envelope_at or envelope_at <= 0:
        return None

    k = float(params["cycle_amplitude_k"])
    ratio = fwd / envelope_at
    directional = efficiency >= params["trend_efficiency"]

    if ratio >= k:
        return "breakout" if directional else "exhaustion"
    if ratio <= 1 / k:
        return "compression"
    return "trend" if directional else "compression"


def cycle_verdict(claimed: str | None, realized: str | None) -> str | None:
    """Five errors that differ in what they cost, mirroring regime_verdict.

    Both labels decompose into (expanding?, directional?), so the distance
    between a claim and the truth is just which bits are wrong.
    """
    if claimed not in CYCLE_EXPANDING or realized not in CYCLE_EXPANDING:
        return None
    if claimed == realized:
        return "exact"

    amp_wrong = CYCLE_EXPANDING[claimed] != CYCLE_EXPANDING[realized]
    dir_wrong = CYCLE_DIRECTIONAL[claimed] != CYCLE_DIRECTIONAL[realized]

    if amp_wrong and dir_wrong:
        return "phase_inversion"      # wrong on both axes; nothing survives it
    if amp_wrong:
        return "amplitude_error"      # right about direction, wrong about scale
    # direction is the wrong bit; which way it is wrong is what it costs
    return ("direction_overcall" if CYCLE_DIRECTIONAL[claimed]
            else "direction_undercall")


def score_path(stage1: dict, stage2: dict, anchor, atr, params: dict) -> dict:
    """Per-node verdicts for the decision checklist.

    Not a forward-looking grader: it asks whether the model's own answers
    match the numbers it returned in the same breath, which is decidable
    the instant the reply lands. The sample is therefore every analysis
    that carries a path, and every node it answered - a much larger n than
    the trade grader will reach for a long time.

    Each node scores `agree`, `contradicted`, or `unchecked`. `unchecked`
    is honest rather than lazy: a no_trade has no entry, so three of the
    four questions have no geometry to test, and stop_placement's "beyond
    a real swing" needs swing detection this codebase does not have.
    """
    path = stage2.get("decision_path")
    if not path:
        return {"path_nodes_answered": None, "path_contradictions": None,
                "path_json": None}

    answers = {step.get("node"): step.get("answer") for step in path
               if isinstance(step, dict)}
    reference = stage2.get("entry")
    if reference is None:
        reference = anchor
    errs = consistency_errors(stage2, stage1, atr, anchor)

    verdicts, contradicted = {}, 0
    for node in PATH_NODES:
        answer = answers.get(node)
        if answer is None:
            verdicts[node] = None
            continue
        hit = any(node in e for e in errs)
        if hit:
            verdicts[node] = "contradicted"
            contradicted += 1
        elif answer == "na":
            verdicts[node] = "unchecked"
        else:
            verdicts[node] = "agree"

    answered = sum(1 for v in verdicts.values() if v in ("agree", "contradicted"))
    return {
        "path_nodes_answered": answered,
        "path_contradictions": contradicted,
        "path_json": {"answers": answers, "verdicts": verdicts,
                      "distance_atr": nearest_level_distance(
                          reference, atr, stage1.get("key_levels"))
                      if reference and atr else None},
    }


def regime_verdict(claimed: str | None, realized: str | None) -> str | None:
    """Collapse the 4x4 confusion matrix into the five errors that differ
    in what they cost. 16 cells over 25 rows is noise; these are not.
    """
    if claimed is None or realized is None:
        return None
    if claimed == realized:
        return "exact"
    if claimed in TREND_REGIMES and realized in TREND_REGIMES:
        return "inversion"          # every downstream decision inherits it
    if claimed in TREND_REGIMES:
        return "false_trend"        # overtrading risk
    if realized in TREND_REGIMES:
        return "missed_trend"       # under-participation
    return "amplitude_error"        # range <-> chop; nearly the same trade


# --- the barrier test, shared by graders 1 and 2 ------------------------

def barrier_walk(anchor: float, atr: float, bars: list[dict], long: bool,
                 params: dict, horizon: int | None = None
                 ) -> tuple[str | None, int | None]:
    """Which barrier is reached first, walking forward bar by bar.

    `horizon` is explicit rather than read from params, because the
    barrier test runs on the abstention horizon while the regime grader
    runs on the longer one. Reading a single "the horizon" out of params
    here is exactly the confusion this signature exists to prevent.

    Returns ("target"|"stop"|None, bars_taken). None is a timeout - the
    window ended with neither reached, which is not a win and not a loss.

    Same-bar ties go to the stop, inherited from paper.py: without tick
    data the true order is unknowable, so score against yourself.
    """
    target_move = params["target_atr"] * atr
    stop_move = params["stop_atr"] * atr
    target = anchor + target_move if long else anchor - target_move
    stop = anchor - stop_move if long else anchor + stop_move

    if horizon is None:
        horizon = params["abstention_horizon_bars"]
    for i, bar in enumerate(bars[:int(horizon)], start=1):
        stop_hit = bar["low"] <= stop if long else bar["high"] >= stop
        target_hit = bar["high"] >= target if long else bar["low"] <= target
        if stop_hit:
            return "stop", i
        if target_hit:
            return "target", i
    return None, None


# --- grader 2: was declining right --------------------------------------

def abstention_outcome(anchor: float, atr: float, bars: list[dict],
                       claimed_regime: str | None, params: dict) -> dict:
    """Did a payable move exist at all - in either direction?

    No counterfactual trade is constructed. Inventing one means inventing
    a direction, a level and a stop; instead the strategy's own geometry
    (1.5 target, 1.0 stop) is applied to both sides and we ask whether
    either would have resolved to target.

    The two sides are mutually exclusive by construction: reaching +1.5
    means passing +1.0 first, which stops the short side out.

    A miss is not automatically an error. If the paying direction
    contradicts the model's own diagnosis, refusing a counter-trend trade
    was correct given that diagnosis - the mistake belongs to grader 3.
    `miss_aligned` keeps the two apart instead of double-charging.
    """
    if not bars or not atr or atr <= 0:
        return {"abstention_outcome": "insufficient_data", "missed_direction": None,
                "miss_aligned": None, "bars_to_payoff": None}

    horizon = int(params["abstention_horizon_bars"])
    long_result, long_bars = barrier_walk(anchor, atr, bars, True, params, horizon)
    short_result, short_bars = barrier_walk(anchor, atr, bars, False, params, horizon)

    if long_result == "target":
        direction, bars_to = "long", long_bars
    elif short_result == "target":
        direction, bars_to = "short", short_bars
    else:
        return {"abstention_outcome": "correct", "missed_direction": None,
                "miss_aligned": None, "bars_to_payoff": None}

    aligned = None
    if claimed_regime in TREND_REGIMES:
        aligned = int((claimed_regime == "bull_trend") == (direction == "long"))

    return {"abstention_outcome": f"miss_{direction}", "missed_direction": direction,
            "miss_aligned": aligned, "bars_to_payoff": bars_to}


# --- grader 1: entries ---------------------------------------------------

def trade_outcome(symbol: str, decision: dict, bar_ts: int, atr: float,
                  bars: list[dict], params: dict) -> dict:
    """Walk a real decision forward using the live fill logic.

    paper.on_bar is not re-implemented here on purpose: it already owns
    the fill predicates and the same-bar rule, and a second copy would
    drift from the live paper trader, quietly invalidating every
    comparison between paper results and scores.

    Two clocks. The order has fill_window_bars to fill, measured from the
    decision; once filled it has horizon_bars to resolve, measured from
    the FILL - a trade should not be scored as a timeout for filling late.
    """
    blank = {"trade_outcome": "not_applicable", "filled_ts": None,
             "bars_to_fill": None, "exit_ts": None, "bars_to_exit": None,
             "r_multiple": None, "mtm_r": None, "trade_mae_r": None,
             "trade_mfe_r": None, "entry_distance_atr": None,
             "same_bar_ambiguous": 0}

    trade = paper.trade_from_decision(symbol, decision, bar_ts)
    if trade is None:
        return blank
    if not bars:
        return {**blank, "trade_outcome": "insufficient_data"}

    fill_window = int(params["fill_window_bars"])
    horizon = int(params["horizon_bars"])
    is_long = trade["direction"] == "long"

    closest = None                 # nearest approach to the entry, in price
    fill_index = None
    mae = mfe = None
    ambiguous = 0
    last_close = None

    for i, bar in enumerate(bars, start=1):
        if trade["status"] == "pending":
            if i > fill_window:
                break
            gap = 0.0 if bar["low"] <= trade["entry"] <= bar["high"] else min(
                abs(bar["low"] - trade["entry"]), abs(bar["high"] - trade["entry"]))
            closest = gap if closest is None else min(closest, gap)

        was_pending = trade["status"] == "pending"
        paper.on_bar(trade, bar)

        if was_pending and trade["status"] != "pending":
            if trade["status"] == "expired":
                break
            fill_index = i

        if fill_index is not None:
            # excursions in R, from the fill, over the bars we were in
            risk = abs(trade["entry"] - trade["stop"]) or 1e-9
            up = (bar["high"] - trade["entry"]) / risk
            down = (bar["low"] - trade["entry"]) / risk
            favourable, adverse = (up, down) if is_long else (-down, -up)
            mfe = favourable if mfe is None else max(mfe, favourable)
            mae = adverse if mae is None else min(mae, adverse)
            last_close = bar["close"]

            covers_both = (bar["low"] <= trade["stop"] <= bar["high"]
                           and bar["low"] <= trade["target"] <= bar["high"])
            if covers_both and trade["status"] == "closed":
                ambiguous = 1

        if trade["status"] == "closed":
            break
        if fill_index is not None and i - fill_index >= horizon:
            break

    risk = abs(trade["entry"] - trade["stop"]) or 1e-9
    common = {
        "trade_mae_r": round(mae, 3) if mae is not None else None,
        "trade_mfe_r": round(mfe, 3) if mfe is not None else None,
        "same_bar_ambiguous": ambiguous,
    }

    if trade["status"] == "closed":
        return {**blank, **common,
                "trade_outcome": trade["exit_reason"],
                "filled_ts": trade["filled_ts"], "bars_to_fill": fill_index,
                "exit_ts": trade["closed_ts"],
                "bars_to_exit": None if fill_index is None else _exit_index(bars, trade),
                "r_multiple": trade["r_multiple"]}

    if fill_index is None:
        # never filled. Whether that was bad luck or a fantasy level is
        # the whole question, so record how close price actually came.
        ran_out = len(bars) < fill_window
        return {**blank, **common,
                "trade_outcome": "insufficient_data" if ran_out else "unfilled",
                "entry_distance_atr": (round(closest / atr, 4)
                                       if closest is not None and atr else None)}

    # filled, still open at the end of the window
    resolved_bars = len(bars) - fill_index
    if resolved_bars < horizon:
        return {**blank, **common, "trade_outcome": "insufficient_data",
                "filled_ts": trade["filled_ts"], "bars_to_fill": fill_index}

    move = ((last_close - trade["entry"]) if is_long
            else (trade["entry"] - last_close))
    return {**blank, **common, "trade_outcome": "timeout",
            "filled_ts": trade["filled_ts"], "bars_to_fill": fill_index,
            # a timeout at +0.9R and one at -0.9R are not the same event
            "mtm_r": round(move / risk, 3)}


def _exit_index(bars: list[dict], trade: dict) -> int | None:
    for i, bar in enumerate(bars, start=1):
        if bar["ts"] == trade["closed_ts"]:
            return i
    return None


# --- one analysis -------------------------------------------------------

def score_analysis(analysis: dict, forward_bars: list[dict], params: dict,
                   decision_bar: dict | None = None) -> dict:
    """One analysis, one score row. `forward_bars` must contain only bars
    strictly after the analysis - the caller owns that cut, and
    test_scoring.py holds it to it."""
    stage1 = analysis.get("stage1") or {}
    stage2 = analysis.get("stage2") or {}
    horizon = int(params["horizon_bars"])

    anchor = analysis.get("price_at")
    anchor_source = "stored"
    if anchor is None:
        anchor = (decision_bar or {}).get("close")
        anchor_source = "derived"
    atr = analysis.get("atr_at")

    a_horizon = int(params["abstention_horizon_bars"])
    window = forward_bars[:horizon]
    # both graders must have their full window before a row counts
    reach = max(horizon, a_horizon)
    complete = len(forward_bars) >= reach and anchor is not None and bool(atr)

    measures = forward_measures(anchor, atr, window) if complete else forward_measures(
        anchor or 0.0, 0.0, [])
    realized = classify_regime(measures, params) if complete else None
    claimed = stage1.get("regime")

    # The cycle grader needs one more stored quantity than the regime
    # grader. Rows written before it existed have None, so they score None
    # and drop out - which is how run 6's population stays readable.
    envelope_at = analysis.get("envelope_at")
    claimed_cycle = stage1.get("cycle")
    realized_cycle = (classify_cycle(measures, envelope_at, params)
                      if complete else None)

    horizons = {}
    for n in params["secondary_horizons"]:
        n = int(n)
        if complete and len(forward_bars) >= n and atr:
            m = forward_measures(anchor, atr, forward_bars[:n])
            horizons[str(n)] = {**m, "realized_regime": classify_regime(m, params)}

    decision = stage2.get("decision")
    if not complete:
        trade = {"trade_outcome": "insufficient_data" if decision != "no_trade"
                 else "not_applicable"}
        abstention = {"abstention_outcome": "insufficient_data"
                      if decision == "no_trade" else "not_applicable"}
    elif decision == "no_trade":
        trade = {"trade_outcome": "not_applicable"}
        abstention = abstention_outcome(
            anchor, atr, forward_bars[:a_horizon], claimed, params)
    else:
        trade = trade_outcome(analysis["symbol"], stage2, analysis["ts"], atr,
                              forward_bars, params)
        abstention = {"abstention_outcome": "not_applicable"}

    return {
        "analysis_id": analysis.get("id"),
        "symbol": analysis["symbol"],
        "interval": analysis.get("interval", "1m"),
        "bar_ts": analysis["ts"],
        "price_at": anchor, "atr_at": atr, "anchor_source": anchor_source,
        "bars_available": len(forward_bars),
        "window_end_ts": window[-1]["ts"] if window else None,
        "complete": int(complete),
        **measures,
        "horizons_json": horizons,
        "claimed_regime": claimed,
        "claimed_strength": stage1.get("strength"),
        "decision": decision,
        "confidence": stage2.get("confidence"),
        "entry": stage2.get("entry"), "stop": stage2.get("stop"),
        "target": stage2.get("target"),
        "distance_to_nearest_level_atr": nearest_level_distance(
            anchor, atr, stage1.get("key_levels")) if anchor and atr else None,
        "realized_regime": realized,
        "regime_verdict": regime_verdict(claimed, realized) if complete else None,
        "envelope_at": envelope_at,
        "fwd_envelope_ratio": (round(measures["fwd_envelope_atr"] / envelope_at, 4)
                               if complete and envelope_at
                               and measures.get("fwd_envelope_atr") is not None
                               else None),
        "claimed_cycle": claimed_cycle,
        "realized_cycle": realized_cycle,
        "cycle_verdict": cycle_verdict(claimed_cycle, realized_cycle),
        **score_path(stage1, stage2, anchor, atr, params),
        **{k: v for k, v in _blank_trade().items() if k not in trade},
        **trade,
        **{k: v for k, v in _blank_abstention().items() if k not in abstention},
        **abstention,
    }


def _blank_trade() -> dict:
    return {"trade_outcome": None, "filled_ts": None, "bars_to_fill": None,
            "exit_ts": None, "bars_to_exit": None, "r_multiple": None,
            "mtm_r": None, "trade_mae_r": None, "trade_mfe_r": None,
            "entry_distance_atr": None, "same_bar_ambiguous": 0}


def _blank_abstention() -> dict:
    return {"abstention_outcome": None, "missed_direction": None,
            "miss_aligned": None, "bars_to_payoff": None}


# --- baselines: the numbers that make the rates mean anything -----------

def baselines(bars: list[dict], params: dict, interval_ms: int | None = None) -> dict:
    """Run both graders' tests over EVERY bar, not just decision bars.

    A miss rate without this is uninterpretable. If 40% of arbitrary bars
    pay and the model's no_trade bars pay 40% of the time, its abstention
    carries no information. Costs nothing: pure arithmetic, no LLM.
    """
    horizon = int(params["horizon_bars"])                    # regime grader
    a_horizon = int(params["abstention_horizon_bars"])        # barrier test
    reach = max(horizon, a_horizon)
    atrs = atr_series(bars, 14)
    payable = long_paid = short_paid = 0
    tested = regime_tested = skipped = 0
    regimes: dict[str, int] = {}
    cycles: dict[str, int] = {}

    for i, bar in enumerate(bars):
        if not atrs[i] or atrs[i] <= 0:
            continue
        forward = contiguous_prefix(bars[i + 1:i + 1 + reach], interval_ms)
        if len(forward) < reach and len(bars) - (i + 1) >= reach:
            skipped += 1                    # a gap cut it short, not the series end
        anchor, atr = bar["close"], atrs[i]

        # each grader is counted only where ITS OWN window is complete, so
        # the two denominators below are not interchangeable
        if len(forward) >= a_horizon:
            tested += 1
            if barrier_walk(anchor, atr, forward, True, params, a_horizon)[0] == "target":
                payable += 1
                long_paid += 1
            elif barrier_walk(anchor, atr, forward, False, params, a_horizon)[0] == "target":
                payable += 1
                short_paid += 1

        if len(forward) >= horizon:
            regime_tested += 1
            measures = forward_measures(anchor, atr, forward[:horizon])
            realized = classify_regime(measures, params)
            if realized:
                regimes[realized] = regimes.get(realized, 0) + 1
            # the baseline's backward envelope may be recomputed here -
            # there is no analysis for it to drift from, unlike in
            # score_analysis where it must come off the stored row
            prior = envelope_atr(bars[max(0, i - horizon + 1):i + 1], atr)
            realized_cycle = classify_cycle(measures, prior, params)
            if realized_cycle:
                cycles[realized_cycle] = cycles.get(realized_cycle, 0) + 1

    majority = max(regimes.values()) / regime_tested if regime_tested and regimes else None
    return {
        "horizon_bars": horizon,
        "abstention_horizon_bars": a_horizon,
        # the barrier test's denominator; the regime one is separate below
        "bars_tested": tested,
        "regime_bars_tested": regime_tested,
        # windows that had enough bars but were cut short by a gap; a
        # large number here means the series is not one continuous session
        "windows_skipped_for_gaps": skipped,
        "payable_rate": round(payable / tested, 4) if tested else None,
        "payable_long_rate": round(long_paid / tested, 4) if tested else None,
        "payable_short_rate": round(short_paid / tested, 4) if tested else None,
        "regime_counts": regimes,
        "majority_regime_rate": round(majority, 4) if majority else None,
        "majority_regime": max(regimes, key=regimes.get) if regimes else None,
        "cycle_counts": cycles,
        "majority_cycle_rate": (round(max(cycles.values()) / sum(cycles.values()), 4)
                                if cycles else None),
        "majority_cycle": max(cycles, key=cycles.get) if cycles else None,
    }


def sweep_cycle_k(bars: list[dict], interval_ms: int | None,
                  ks, params: dict | None = None) -> list[dict]:
    """Realized-cycle distribution across a grid of amplitude bands.

    The point of choosing k is to land a grader that can be wrong. A k
    that makes one label swallow 95% of windows produces a majority
    baseline nothing can beat, which is the shape score run 6's regime
    grader ran into - so this reports the majority rate at each k and the
    caller picks before any model output is involved.

    Run it on a DIFFERENT series from the one to be scored. Run 6's
    abstention barriers were swept on the series they were then measured
    against, and that caveat is still in the doc.
    """
    base = resolve_params(params)
    out = []
    for k in ks:
        b = baselines(bars, {**base, "cycle_amplitude_k": float(k)}, interval_ms)
        out.append({"k": float(k), "counts": b["cycle_counts"],
                    "majority_rate": b["majority_cycle_rate"],
                    "majority_cycle": b["majority_cycle"],
                    "windows": sum(b["cycle_counts"].values())})
    return out


def sweep_baselines(bars: list[dict], interval_ms: int | None,
                    horizons, scales, base_params: dict | None = None) -> list[dict]:
    """Base rate across a grid of horizons and barrier sizes.

    The barrier pair is scaled as a UNIT - target 1.5k, stop 1.0k - so the
    strategy's 1.5 reward:risk is preserved and only the size of the test
    changes, not its shape. Scaling only the target would be sweeping a
    different strategy, not calibrating this one.

    Read the result as discriminating power. A base rate near 1.0 means
    almost every bar "pays" and the abstention grader cannot tell a good
    no_trade from a bad one; near 0.0 it fires too rarely to have any
    resolution. Somewhere in the middle is where a miss carries
    information.

    Cheap enough to run freely: pure arithmetic over stored bars, no LLM.
    """
    base = dict(base_params or DEFAULTS)
    atrs = atr_series(bars, 14)
    out = []
    for horizon in horizons:
        horizon = int(horizon)
        for scale in scales:
            params = {**base, "horizon_bars": horizon,
                      "target_atr": base["target_atr"] * scale,
                      "stop_atr": base["stop_atr"] * scale}
            tested = paid = skipped = 0
            for i, bar in enumerate(bars):
                forward = contiguous_prefix(bars[i + 1:i + 1 + horizon], interval_ms)
                if len(forward) < horizon:
                    if len(bars) - (i + 1) >= horizon:
                        skipped += 1
                    continue
                if not atrs[i] or atrs[i] <= 0:
                    continue
                tested += 1
                anchor, atr = bar["close"], atrs[i]
                if (barrier_walk(anchor, atr, forward, True, params, horizon)[0] == "target"
                        or barrier_walk(anchor, atr, forward, False, params, horizon)[0] == "target"):
                    paid += 1
            out.append({
                "horizon_bars": horizon,
                "scale": scale,
                "target_atr": round(params["target_atr"], 3),
                "stop_atr": round(params["stop_atr"], 3),
                "bars_tested": tested,
                "windows_skipped_for_gaps": skipped,
                "payable_rate": round(paid / tested, 4) if tested else None,
            })
    return out


# --- how much of a sample this actually is ------------------------------

def independent_windows(bar_timestamps, horizon_bars: int, interval_ms: int) -> int:
    """The largest set of forward windows that do not overlap.

    25 consecutive 1m analyses scored over 30 bars share 29 of every 30
    bars. They are not 25 observations, and every count in the summary is
    reported next to this number so nobody reads them as if they were.
    """
    span = int(horizon_bars) * int(interval_ms)
    count, free_from = 0, None
    for ts in sorted(bar_timestamps):
        if free_from is None or ts >= free_from:
            count += 1
            free_from = ts + span
    return count


# What each headline number needs before it means anything. Judgement
# calls, stated here rather than buried in a report template. Rows alone
# are not enough: overlapping windows inflate row counts without adding
# information, so both gates have to clear.
REQUIREMENTS = {
    "trade_win_rate": (100, 30),
    "abstention_lift": (20, 5),
    "regime_accuracy": (20, 5),
    # Same shape as regime_accuracy: the same window, the same
    # majority-baseline comparison, so the same evidence bar.
    "cycle_accuracy": (20, 5),
    "regime_matrix": (480, 100),
    "confidence_calibration": (150, 50),
}


def _verdict(name: str, rows: int, independent: int, subject: str) -> dict:
    need_rows, need_independent = REQUIREMENTS[name]
    ok = rows >= need_rows and independent >= need_independent
    if ok:
        note = f"{rows} rows over {independent} independent windows supports {subject}."
    elif independent < need_independent:
        note = (f"Cannot support {subject}: {rows} rows, but only {independent} "
                f"independent {'window' if independent == 1 else 'windows'} "
                f"({need_independent} needed). Overlapping forward windows inflate "
                "the row count without adding information - raise the replay stride.")
    else:
        note = (f"Cannot support {subject}: {rows} rows, {need_rows} needed "
                f"({independent} independent windows is enough).")
    return {"rows": rows, "independent_windows": independent,
            "sufficient": ok, "note": note}


def _counts(rows, key) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        v = r.get(key)
        if v and v != "not_applicable":
            out[v] = out.get(v, 0) + 1
    return out


def summarize(rows: list[dict], params: dict, interval_ms: int,
              base: dict | None = None) -> dict:
    """Counts, each one carrying how many independent windows produced it,
    and a plain sentence when a number cannot support a claim."""
    horizon = int(params["horizon_bars"])
    a_horizon = int(params["abstention_horizon_bars"])
    scored = [r for r in rows if r["complete"]]

    # Independence is a property of the WINDOW, so each grader is counted
    # at its own horizon. A 10-bar window frees up sooner than a 30-bar
    # one, and 25 consecutive decisions are worth more observations to the
    # abstention grader than to the regime grader for exactly that reason.
    def windows(subset, h=None):
        return independent_windows([r["bar_ts"] for r in subset],
                                   horizon if h is None else h, interval_ms)

    trades = [r for r in scored if r["trade_outcome"] in ("target", "stop")]
    resolved = _counts(scored, "trade_outcome")
    wins = sum(1 for r in trades if (r["r_multiple"] or 0) > 0)

    abstentions = [r for r in scored if r["abstention_outcome"] in
                   ("correct", "miss_long", "miss_short")]
    misses = [r for r in abstentions if r["abstention_outcome"] != "correct"]
    aligned = [r for r in misses if r["miss_aligned"] == 1]
    at_level = [r for r in misses
                if (r["distance_to_nearest_level_atr"] is not None
                    and r["distance_to_nearest_level_atr"] <= params["level_proximity_atr"])]
    miss_rate = round(len(misses) / len(abstentions), 4) if abstentions else None

    lift = None
    if miss_rate is not None and base and base.get("payable_rate"):
        lift = round(1 - (miss_rate / base["payable_rate"]), 4)

    regimes = [r for r in scored if r["regime_verdict"]]
    exact = sum(1 for r in regimes if r["regime_verdict"] == "exact")

    cycles = [r for r in scored if r.get("cycle_verdict")]
    cycle_exact = sum(1 for r in cycles if r["cycle_verdict"] == "exact")
    realized_cycles = _counts(cycles, "realized_cycle")
    cycle_majority = ((max(realized_cycles.values()) / len(cycles))
                      if realized_cycles else None)

    # The path grader does not need a complete forward window - it asks
    # whether the reply contradicted itself, which was decidable the
    # moment it landed. So it runs over every row, not just scored ones.
    paths = [r for r in rows if r.get("path_nodes_answered") is not None]
    contradicted = sum(1 for r in paths if (r.get("path_contradictions") or 0) > 0)
    nodes_answered = sum(r.get("path_nodes_answered") or 0 for r in paths)
    nodes_contradicted = sum(r.get("path_contradictions") or 0 for r in paths)

    # What a constant predictor would have scored ON THESE ROWS. It has to
    # be the same population the accuracy is measured over: comparing 25
    # scored rows against the majority class of a thousand other bars is
    # comparing two different questions, and whichever way it lands the
    # comparison is not evidence. The table-wide distribution is still
    # reported, one field down, as context - never as the baseline.
    realized_here = _counts(regimes, "realized_regime")
    majority_here = (max(realized_here.values()) / len(regimes)) if realized_here else None

    return {
        "analyses": len(rows),
        "scored": len(scored),
        "incomplete": len(rows) - len(scored),
        # Two horizons, stated here and again on every section that uses
        # one. The graders were decoupled because ATR14 is a bar-scale
        # unit and a 30-bar barrier test built on it fires on 80% of bars.
        # The cost is that abstention and regime results are NOT
        # cross-tabulable: they describe different windows over the same
        # decision, and a row that is a "miss" at 10 bars and a "range" at
        # 30 is not a contradiction.
        "horizon_bars": horizon,
        "abstention_horizon_bars": a_horizon,
        "horizons_note": (
            f"regime and trade are scored over {horizon} bars, abstention over "
            f"{a_horizon}. Do not cross-tabulate them: they are different "
            "windows on the same decision."),
        # reported at the longer horizon, which is the conservative read
        "independent_windows": windows(scored),
        "baselines": base or {},
        "trade": {
            "outcomes": resolved,
            "wins": wins,
            "win_rate": round(wins / len(trades), 4) if trades else None,
            "total_r": round(sum(r["r_multiple"] or 0 for r in trades), 3),
            "same_bar_ambiguous": sum(r["same_bar_ambiguous"] or 0 for r in scored),
            "horizon_bars": horizon,
            **_verdict("trade_win_rate", len(trades), windows(trades),
                       "a win rate or an expectancy"),
        },
        "abstention": {
            "outcomes": _counts(scored, "abstention_outcome"),
            "miss_rate": miss_rate,
            "base_rate": (base or {}).get("payable_rate"),
            "lift": lift,
            "aligned_misses": len(aligned),
            "aligned_misses_at_level": len([r for r in aligned if r in at_level]),
            "horizon_bars": a_horizon,
            "barriers_atr": f'{params["target_atr"]}/{params["stop_atr"]}',
            **_verdict("abstention_lift", len(abstentions),
                       windows(abstentions, a_horizon),
                       "an abstention lift over the base rate"),
        },
        "regime": {
            "verdicts": _counts(scored, "regime_verdict"),
            "claimed": _counts(scored, "claimed_regime"),
            "realized": _counts(scored, "realized_regime"),
            "exact": exact,
            "accuracy": round(exact / len(regimes), 4) if regimes else None,
            # same population as `accuracy`, so the two are comparable
            "majority_baseline": round(majority_here, 4) if majority_here else None,
            "majority_baseline_regime": (max(realized_here, key=realized_here.get)
                                         if realized_here else None),
            "beats_majority": (None if majority_here is None or not regimes
                               else bool(exact / len(regimes) > majority_here)),
            # a DIFFERENT population - every bar in the table. Context for
            # how unusual this window was, not a baseline for the accuracy.
            "table_wide_majority_rate": (base or {}).get("majority_regime_rate"),
            "table_wide_majority_regime": (base or {}).get("majority_regime"),
            "horizon_bars": horizon,
            **_verdict("regime_accuracy", len(regimes), windows(regimes),
                       "a regime accuracy against the majority-class baseline"),
        },
        "cycle": {
            "verdicts": _counts(cycles, "cycle_verdict"),
            "claimed": _counts(scored, "claimed_cycle"),
            "realized": _counts(cycles, "realized_cycle"),
            "exact": cycle_exact,
            "accuracy": round(cycle_exact / len(cycles), 4) if cycles else None,
            # same population as the accuracy, for the same reason it is
            # for regime: a constant predictor measured on other rows is
            # not a baseline, it is a different question
            "majority_baseline": round(cycle_majority, 4) if cycle_majority else None,
            "majority_baseline_cycle": (max(realized_cycles, key=realized_cycles.get)
                                        if realized_cycles else None),
            "beats_majority": (None if cycle_majority is None or not cycles
                               else bool(cycle_exact / len(cycles) > cycle_majority)),
            "amplitude_k": params["cycle_amplitude_k"],
            "horizon_bars": horizon,
            **_verdict("cycle_accuracy", len(cycles), windows(cycles),
                       "a cycle accuracy against the majority-class baseline"),
        },
        "path": {
            "rows_with_path": len(paths),
            "rows_contradicted": contradicted,
            "nodes_answered": nodes_answered,
            "nodes_contradicted": nodes_contradicted,
            "contradiction_rate": (round(nodes_contradicted / nodes_answered, 4)
                                   if nodes_answered else None),
            "by_node": {
                node: _counts(
                    [{"v": (r.get("path_json") or {}).get("verdicts", {}).get(node)}
                     for r in paths], "v")
                for node in PATH_NODES
            },
            # No independence caveat: this grader reads no forward window,
            # so consecutive rows are not correlated through overlapping
            # bars the way every other grader's are.
            "note": ("self-consistency of the reply, not a forward-looking "
                     "result: no forward window is read, so rows do not "
                     "overlap and every answered node counts"),
        },
    }
