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
from .features import atr as atr_series

SCORER_VERSION = "1"

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
    # Read off the stage-2 prompts, not invented here: "risk_reward must
    # be >= 1.5" and "stop must sit... roughly 1x ATR14 from entry". The
    # scorer holds the model to the geometry the model was told to use.
    "target_atr": 1.5,
    "stop_atr": 1.0,
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

    `bars` are the forward bars only. Excursions are signed: mfe >= 0,
    mae <= 0. Everything is in ATR-at-decision units, which is what makes
    rows comparable across symbols, price levels and volatility.
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
                 params: dict) -> tuple[str | None, int | None]:
    """Which barrier is reached first, walking forward bar by bar.

    Returns ("target"|"stop"|None, bars_taken). None is a timeout - the
    window ended with neither reached, which is not a win and not a loss.

    Same-bar ties go to the stop, inherited from paper.py: without tick
    data the true order is unknowable, so score against yourself.
    """
    target_move = params["target_atr"] * atr
    stop_move = params["stop_atr"] * atr
    target = anchor + target_move if long else anchor - target_move
    stop = anchor - stop_move if long else anchor + stop_move

    for i, bar in enumerate(bars[:int(params["horizon_bars"])], start=1):
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

    long_result, long_bars = barrier_walk(anchor, atr, bars, True, params)
    short_result, short_bars = barrier_walk(anchor, atr, bars, False, params)

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

def nearest_level_distance(price: float, atr: float, levels) -> float | None:
    """How far the decision sat from its own diagnosed key levels.

    A hook, not a grader: the range playbook only allows entries near the
    extremes, so a "missed" move from mid-range was never takeable. The
    level grader itself is deliberately not built yet.
    """
    if not levels or not atr or atr <= 0:
        return None
    numeric = [float(x) for x in levels if isinstance(x, (int, float))]
    if not numeric:
        return None
    return round(min(abs(price - x) for x in numeric) / atr, 4)


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

    window = forward_bars[:horizon]
    complete = len(forward_bars) >= horizon and anchor is not None and bool(atr)

    measures = forward_measures(anchor, atr, window) if complete else forward_measures(
        anchor or 0.0, 0.0, [])
    realized = classify_regime(measures, params) if complete else None
    claimed = stage1.get("regime")

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
        abstention = abstention_outcome(anchor, atr, window, claimed, params)
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

def baselines(bars: list[dict], params: dict) -> dict:
    """Run both graders' tests over EVERY bar, not just decision bars.

    A miss rate without this is uninterpretable. If 40% of arbitrary bars
    pay and the model's no_trade bars pay 40% of the time, its abstention
    carries no information. Costs nothing: pure arithmetic, no LLM.
    """
    horizon = int(params["horizon_bars"])
    atrs = atr_series(bars, 14)
    payable = long_paid = short_paid = 0
    tested = 0
    regimes: dict[str, int] = {}

    for i, bar in enumerate(bars):
        forward = bars[i + 1:i + 1 + horizon]
        if len(forward) < horizon or not atrs[i] or atrs[i] <= 0:
            continue
        tested += 1
        anchor, atr = bar["close"], atrs[i]

        if barrier_walk(anchor, atr, forward, True, params)[0] == "target":
            payable += 1
            long_paid += 1
        elif barrier_walk(anchor, atr, forward, False, params)[0] == "target":
            payable += 1
            short_paid += 1

        realized = classify_regime(forward_measures(anchor, atr, forward), params)
        if realized:
            regimes[realized] = regimes.get(realized, 0) + 1

    majority = max(regimes.values()) / tested if tested and regimes else None
    return {
        "bars_tested": tested,
        "payable_rate": round(payable / tested, 4) if tested else None,
        "payable_long_rate": round(long_paid / tested, 4) if tested else None,
        "payable_short_rate": round(short_paid / tested, 4) if tested else None,
        "regime_counts": regimes,
        "majority_regime_rate": round(majority, 4) if majority else None,
        "majority_regime": max(regimes, key=regimes.get) if regimes else None,
    }


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
    scored = [r for r in rows if r["complete"]]

    def windows(subset):
        return independent_windows([r["bar_ts"] for r in subset], horizon, interval_ms)

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

    return {
        "analyses": len(rows),
        "scored": len(scored),
        "incomplete": len(rows) - len(scored),
        "horizon_bars": horizon,
        "independent_windows": windows(scored),
        "baselines": base or {},
        "trade": {
            "outcomes": resolved,
            "wins": wins,
            "win_rate": round(wins / len(trades), 4) if trades else None,
            "total_r": round(sum(r["r_multiple"] or 0 for r in trades), 3),
            "same_bar_ambiguous": sum(r["same_bar_ambiguous"] or 0 for r in scored),
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
            **_verdict("abstention_lift", len(abstentions), windows(abstentions),
                       "an abstention lift over the base rate"),
        },
        "regime": {
            "verdicts": _counts(scored, "regime_verdict"),
            "claimed": _counts(scored, "claimed_regime"),
            "realized": _counts(scored, "realized_regime"),
            "exact": exact,
            "accuracy": round(exact / len(regimes), 4) if regimes else None,
            "majority_baseline": (base or {}).get("majority_regime_rate"),
            **_verdict("regime_accuracy", len(regimes), windows(regimes),
                       "a regime accuracy against the majority-class baseline"),
        },
    }
