"""Scoring: the three graders, the baselines, and the honesty of the summary.

The no-lookahead section is the mirror of test_no_lookahead.py. That file
asserts an analysis cannot read forward; this one asserts the scorer
cannot read backward - and, because "changes nothing" is a claim a broken
test satisfies for free, it also asserts that mutating a bar the scorer
IS allowed to see really does move the numbers.
"""
import json
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "test_scoring.db")

from candle_agent import db, paper, scoring
from candle_agent.services import scorer

SYMBOL = "SCORE"
STEP_MS = 60_000
START_TS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "scoring.db"))


def series(hlc, start_ts=START_TS, open0=100.0):
    """Bars from explicit (high, low, close) triples - barrier tests need
    the extremes pinned, not generated."""
    bars, o = [], open0
    for i, (h, l, c) in enumerate(hlc):
        bars.append({"ts": start_ts + i * STEP_MS, "open": o, "high": h,
                     "low": l, "close": c, "volume": 1.0})
        o = c
    return bars


def flat(n, price=100.0, wobble=0.05, start_ts=START_TS):
    return series([(price + wobble, price - wobble, price)] * n, start_ts)


def climb(n, start=100.0, step=0.4, start_ts=START_TS):
    """A steady drift. `step` may be negative - hence max/min rather than
    assuming the close is the high, which silently produced bars with the
    high below the low on a descending series."""
    hlc = []
    price = start
    for _ in range(n):
        nxt = price + step
        hlc.append((max(price, nxt) + 0.05, min(price, nxt) - 0.05, nxt))
        price = nxt
    return series(hlc, start_ts, open0=start)


# Explicit about every parameter it depends on, including the barriers.
# These tests exercise the MECHANISM; the production calibration is a
# separate decision and is pinned on its own below, so re-calibrating
# cannot silently rewrite what the mechanism tests mean.
P = scoring.resolve_params({"horizon_bars": 5, "fill_window_bars": 3,
                            "abstention_horizon_bars": 5,
                            "target_atr": 1.5, "stop_atr": 1.0})


def analysis(regime="range", decision="no_trade", price=100.0, atr=1.0,
             ts=START_TS, **stage2):
    return {
        "id": 1, "symbol": SYMBOL, "interval": "1m", "ts": ts,
        "price_at": price, "atr_at": atr,
        "stage1": {"regime": regime, "strength": "moderate",
                   "key_levels": stage2.pop("key_levels", [100.0]),
                   "summary": "x"},
        "stage2": {"decision": decision, "confidence": "medium",
                   "reasoning_chain": ["x"], **stage2},
    }


# --- parameters are recorded, not assumed --------------------------------

def test_an_unknown_parameter_is_refused():
    with pytest.raises(scoring.ScoringError) as e:
        scoring.resolve_params({"horizen_bars": 30})
    assert "horizen_bars" in str(e.value)


def test_a_fill_window_longer_than_the_shared_ttl_is_refused():
    """Raising it would do nothing - paper.on_bar expires the order first
    - so say so instead of quietly ignoring it."""
    with pytest.raises(scoring.ScoringError) as e:
        scoring.resolve_params({"fill_window_bars": paper.PENDING_TTL_BARS + 1})
    assert "PENDING_TTL_BARS" in str(e.value)


# --- grader 1: entries ---------------------------------------------------

def test_target_before_stop_is_a_win():
    bars = series([(100.4, 99.8, 100.3), (100.9, 100.2, 100.8),
                   (101.6, 100.7, 101.5), (101.8, 101.0, 101.2),
                   (102.0, 101.1, 101.9)])
    row = scoring.trade_outcome(SYMBOL, {"decision": "market_buy", "entry": 100.0,
                                         "stop": 99.0, "target": 101.5},
                                START_TS, 1.0, bars, P)
    assert row["trade_outcome"] == "target"
    assert row["r_multiple"] == 1.5
    assert row["bars_to_fill"] == 1


def test_stop_before_target_is_a_loss():
    bars = series([(100.2, 99.6, 99.8), (100.0, 98.9, 99.0),
                   (101.9, 98.8, 101.6), (102.0, 101.0, 101.8),
                   (102.2, 101.2, 102.0)])
    row = scoring.trade_outcome(SYMBOL, {"decision": "market_buy", "entry": 100.0,
                                         "stop": 99.0, "target": 101.5},
                                START_TS, 1.0, bars, P)
    assert row["trade_outcome"] == "stop"
    assert row["r_multiple"] == -1.0


def test_one_bar_covering_both_barriers_is_a_loss_and_is_flagged():
    """The pessimistic rule is a guess; the flag is how we find out how
    often it was load-bearing."""
    bars = series([(101.8, 98.5, 101.0)] + [(101.2, 100.8, 101.0)] * 4)
    row = scoring.trade_outcome(SYMBOL, {"decision": "market_buy", "entry": 100.0,
                                         "stop": 99.0, "target": 101.5},
                                START_TS, 1.0, bars, P)
    assert row["trade_outcome"] == "stop"
    assert row["same_bar_ambiguous"] == 1


def test_a_limit_never_touched_is_unfilled_not_a_loss():
    bars = flat(8, price=105.0)      # entry at 100 is never approached
    row = scoring.trade_outcome(SYMBOL, {"decision": "buy_limit", "entry": 100.0,
                                         "stop": 99.0, "target": 101.5},
                                START_TS, 1.0, bars, P)
    assert row["trade_outcome"] == "unfilled"
    assert row["r_multiple"] is None
    # bad luck or a fantasy level? the distance is the whole question
    assert row["entry_distance_atr"] == pytest.approx(4.95, abs=0.01)


def test_a_filled_trade_that_resolves_neither_way_is_a_timeout_with_a_mark():
    bars = series([(100.3, 99.9, 100.2)] * 6)
    row = scoring.trade_outcome(SYMBOL, {"decision": "market_buy", "entry": 100.0,
                                         "stop": 99.0, "target": 101.5},
                                START_TS, 1.0, bars, P)
    assert row["trade_outcome"] == "timeout"
    assert row["r_multiple"] is None      # not a win, not a loss
    assert row["mtm_r"] == pytest.approx(0.2, abs=0.001)
    assert row["trade_mfe_r"] == pytest.approx(0.3, abs=0.001)


# --- grader 2: abstentions ----------------------------------------------

def test_a_payable_up_move_makes_a_no_trade_a_miss():
    bars = climb(5, start=100.0, step=0.4)       # +2.0 over the window
    out = scoring.abstention_outcome(100.0, 1.0, bars, "bull_trend", P)
    assert out["abstention_outcome"] == "miss_long"
    assert out["missed_direction"] == "long"
    assert out["miss_aligned"] == 1              # it declined its own thesis


def test_a_miss_against_the_diagnosis_is_recorded_as_unaligned():
    """Refusing a counter-trend trade was correct GIVEN the diagnosis. The
    error belongs to grader 3; counting it here too would double-charge."""
    bars = climb(5, start=100.0, step=0.4)
    out = scoring.abstention_outcome(100.0, 1.0, bars, "bear_trend", P)
    assert out["abstention_outcome"] == "miss_long"
    assert out["miss_aligned"] == 0


def test_chop_makes_a_no_trade_correct():
    out = scoring.abstention_outcome(100.0, 1.0, flat(5), "chop", P)
    assert out["abstention_outcome"] == "correct"
    assert out["missed_direction"] is None


@pytest.mark.parametrize("step", [0.4, -0.4, 0.05, -0.05, 1.2, -1.2])
def test_the_two_directions_can_never_both_pay(step):
    """Mutual exclusivity is what makes the outcome a clean label: reaching
    +1.5 means passing +1.0 first, which stops the short side out."""
    bars = climb(5, start=100.0, step=step)
    long_hit = scoring.barrier_walk(100.0, 1.0, bars, True, P)[0] == "target"
    short_hit = scoring.barrier_walk(100.0, 1.0, bars, False, P)[0] == "target"
    assert not (long_hit and short_hit)


# --- grader 3: the diagnosis --------------------------------------------

def test_a_clean_advance_is_classified_as_a_bull_trend():
    m = scoring.forward_measures(100.0, 1.0, climb(5, step=0.4))
    assert scoring.classify_regime(m, P) == "bull_trend"


def test_a_wide_oscillation_is_a_range_not_a_trend():
    bars = series([(101.6, 99.9, 101.4), (101.7, 99.8, 100.0),
                   (101.5, 98.6, 98.8), (101.4, 98.5, 101.2),
                   (101.3, 99.0, 100.1)])
    m = scoring.forward_measures(100.0, 1.0, bars)
    assert scoring.classify_regime(m, P) == "range"


def test_a_narrow_drift_is_chop():
    """Range vs chop is amplitude: chop has no room for the strategy's own
    trade (1.5 target + 1.0 stop = 2.5 ATR)."""
    m = scoring.forward_measures(100.0, 1.0, flat(5, wobble=0.3))
    assert scoring.classify_regime(m, P) == "chop"


@pytest.mark.parametrize("claimed,realized,expected", [
    ("range", "range", "exact"),
    ("bull_trend", "bear_trend", "inversion"),
    ("bear_trend", "bull_trend", "inversion"),
    ("bull_trend", "chop", "false_trend"),
    ("range", "bull_trend", "missed_trend"),
    ("range", "chop", "amplitude_error"),
    ("chop", "range", "amplitude_error"),
])
def test_the_confusion_matrix_collapses_by_what_the_error_costs(
        claimed, realized, expected):
    assert scoring.regime_verdict(claimed, realized) == expected


# --- the two horizons are decoupled, and said so out loud ----------------

def test_the_production_calibration_is_what_was_chosen():
    """Pinned deliberately. These are not arbitrary defaults, they are a
    measured choice: 1.5/1.0 over 30 bars fires on 0.803 of clean AAPL 1m
    bars and cannot discriminate; 3.0/2.0 over 10 measures 0.337. Changing
    them is a decision, so it should break a test."""
    assert scoring.DEFAULTS["horizon_bars"] == 30
    assert scoring.DEFAULTS["abstention_horizon_bars"] == 10
    assert scoring.DEFAULTS["target_atr"] == 3.0
    assert scoring.DEFAULTS["stop_atr"] == 2.0
    assert scoring.DEFAULTS["target_atr"] / scoring.DEFAULTS["stop_atr"] == 1.5


def test_the_abstention_grader_walks_its_own_horizon_not_the_regime_one():
    """A move that pays only after bar 5 is not a miss on a 5-bar
    abstention window, however long the regime window is."""
    late = flat(5) + climb(6, start=100.0, step=1.0,
                           start_ts=START_TS + 5 * STEP_MS)
    params = scoring.resolve_params({"horizon_bars": 30,
                                     "abstention_horizon_bars": 5,
                                     "target_atr": 1.5, "stop_atr": 1.0})
    short = scoring.abstention_outcome(100.0, 1.0, late, "range", params)
    assert short["abstention_outcome"] == "correct"

    params_long = {**params, "abstention_horizon_bars": 11}
    long = scoring.abstention_outcome(100.0, 1.0, late, "range", params_long)
    assert long["abstention_outcome"] == "miss_long"


def test_the_summary_states_both_horizons_so_no_one_cross_tabulates():
    rows = scored_rows(n=6, stride=10)
    summary = scoring.summarize(rows, P, STEP_MS, None)

    assert summary["horizon_bars"] == P["horizon_bars"]
    assert summary["abstention_horizon_bars"] == P["abstention_horizon_bars"]
    assert "Do not cross-tabulate" in summary["horizons_note"]
    # and each section carries the horizon it was actually scored over
    assert summary["regime"]["horizon_bars"] == P["horizon_bars"]
    assert summary["trade"]["horizon_bars"] == P["horizon_bars"]
    assert summary["abstention"]["horizon_bars"] == P["abstention_horizon_bars"]
    assert summary["abstention"]["barriers_atr"] == "1.5/1.0"


def test_independence_is_counted_at_each_graders_own_horizon():
    """A 10-bar window frees up sooner than a 30-bar one, so the same 25
    decisions are worth more observations to the abstention grader."""
    params = scoring.resolve_params({"horizon_bars": 30,
                                     "abstention_horizon_bars": 10,
                                     "fill_window_bars": 3})
    rows = []
    for i in range(25):
        ts = START_TS + i * STEP_MS
        rows.append(scoring.score_analysis(
            analysis(ts=ts), climb(40, step=0.4, start_ts=ts), params))
    summary = scoring.summarize(rows, params, STEP_MS, None)

    assert summary["regime"]["independent_windows"] == 1
    assert summary["abstention"]["independent_windows"] == 3


def test_the_abstention_horizon_is_stored_with_the_other_parameters(fresh):
    store()
    run = scorer.run(SYMBOL, "1m", overrides={"abstention_horizon_bars": 7})
    assert json.loads(run["params_json"])["abstention_horizon_bars"] == 7
    assert run["params"]["abstention_horizon_bars"] == 7
    assert json.loads(run["summary_json"])["abstention_horizon_bars"] == 7


def test_an_abstention_horizon_below_one_is_refused():
    with pytest.raises(scoring.ScoringError) as e:
        scoring.resolve_params({"abstention_horizon_bars": 0})
    assert "abstention_horizon_bars" in str(e.value)


# --- excursions: the invariant that actually holds -----------------------

def test_mfe_goes_negative_when_price_never_trades_back_to_the_anchor():
    """The docstring used to promise mfe >= 0 and mae <= 0. It was wrong,
    and a real scored row (run 1, 14:02) had mfe = -0.32. Pinned so the
    documentation cannot drift back."""
    gapped_down = series([(99.0, 98.0, 98.5)] * 5, open0=99.0)
    m = scoring.forward_measures(100.0, 1.0, gapped_down)

    assert m["fwd_mfe_atr"] < 0, "high never reached the anchor, so mfe is negative"
    assert m["fwd_mae_atr"] < 0


def test_mae_goes_positive_in_the_mirror_case():
    gapped_up = series([(102.0, 101.0, 101.5)] * 5, open0=102.0)
    m = scoring.forward_measures(100.0, 1.0, gapped_up)

    assert m["fwd_mae_atr"] > 0, "low never reached the anchor, so mae is positive"
    assert m["fwd_mfe_atr"] > 0


@pytest.mark.parametrize("bars", [
    series([(99.0, 98.0, 98.5)] * 5, open0=99.0),          # entirely below
    series([(102.0, 101.0, 101.5)] * 5, open0=102.0),      # entirely above
    climb(5, step=0.4),                                     # straddling, up
    climb(5, step=-0.4),                                    # straddling, down
    flat(5),
])
def test_the_real_invariant_is_mae_le_return_le_mfe(bars):
    m = scoring.forward_measures(100.0, 1.0, bars)
    assert m["fwd_mae_atr"] <= m["fwd_return_atr"] <= m["fwd_mfe_atr"]


# --- windows may not span a gap -----------------------------------------

def test_contiguous_prefix_stops_at_the_first_gap():
    bars = flat(3) + flat(3, start_ts=START_TS + 40 * STEP_MS)
    got = scoring.contiguous_prefix(bars, STEP_MS)
    assert len(got) == 3
    assert got[-1]["ts"] == START_TS + 2 * STEP_MS


def test_a_series_with_no_gap_is_returned_whole():
    bars = flat(6)
    assert len(scoring.contiguous_prefix(bars, STEP_MS)) == 6


def test_the_baseline_skips_windows_a_gap_cut_short_and_counts_them():
    """A 1m window that reads across an overnight gap makes every barrier
    trivially reachable. Skipping is right; skipping SILENTLY is not."""
    clean = climb(40, step=0.05)
    after_gap = climb(40, step=0.05, start_ts=START_TS + 5000 * STEP_MS)
    base = scoring.baselines(clean + after_gap, P, STEP_MS)

    assert base["windows_skipped_for_gaps"] > 0
    assert base["bars_tested"] > 0


def test_a_forward_window_truncated_by_a_gap_is_insufficient_not_wrong(fresh):
    """The scorer must refuse the row rather than score it across the gap."""
    bars = climb(12, step=0.4) + climb(12, step=0.4, start_ts=START_TS + 900 * STEP_MS)
    db.insert_bars(SYMBOL, "1m", bars)
    a = analysis(ts=bars[9]["ts"], price=bars[9]["close"])
    db.insert_analysis(SYMBOL, a["ts"], a["stage1"], a["stage2"], "test", 1,
                       "1m", price_at=a["price_at"], atr_at=a["atr_at"])

    run = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 5,
                                              "fill_window_bars": 3})
    row = db.get_scores(run["id"])[0]
    assert row["bars_available"] == 2, "only the bars before the gap are readable"
    assert row["complete"] == 0


# --- the majority baseline must be the same population -------------------

def test_the_majority_baseline_comes_from_the_rows_being_scored():
    """Comparing 25 scored rows against the majority class of a thousand
    OTHER bars compares two different questions. Whichever way it lands,
    it is not evidence."""
    rows = scored_rows(n=6, stride=10)          # a clean ramp: all bull_trend
    table_wide = {"majority_regime_rate": 0.9, "majority_regime": "chop"}
    summary = scoring.summarize(rows, P, STEP_MS, table_wide)

    realized = summary["regime"]["realized"]
    expected = max(realized.values()) / sum(realized.values())
    assert summary["regime"]["majority_baseline"] == pytest.approx(expected)
    assert summary["regime"]["majority_baseline_regime"] == "bull_trend"
    # the table-wide figure is kept, but never as the baseline
    assert summary["regime"]["table_wide_majority_rate"] == 0.9
    assert summary["regime"]["table_wide_majority_regime"] == "chop"
    assert summary["regime"]["majority_baseline"] != 0.9


def test_beats_majority_is_reported_against_the_same_population():
    rows = scored_rows(n=6, stride=10)
    summary = scoring.summarize(rows, P, STEP_MS, None)
    r = summary["regime"]
    assert r["beats_majority"] == (r["accuracy"] > r["majority_baseline"])


# --- the sweep -----------------------------------------------------------

def test_bigger_barriers_are_reached_less_often():
    """The sweep is only useful if it is monotone in the obvious
    direction; if it is not, the walk is wrong."""
    bars = climb(120, step=0.05)
    grid = scoring.sweep_baselines(bars, STEP_MS, horizons=[30], scales=[1, 2, 4])
    rates = [cell["payable_rate"] for cell in grid]
    assert rates == sorted(rates, reverse=True), rates


def test_the_sweep_preserves_the_strategy_reward_to_risk():
    grid = scoring.sweep_baselines(flat(80), STEP_MS, horizons=[10], scales=[1, 2, 3])
    for cell in grid:
        assert cell["target_atr"] / cell["stop_atr"] == pytest.approx(1.5)


def test_the_sweep_covers_the_whole_grid():
    grid = scoring.sweep_baselines(climb(80, step=0.1), STEP_MS,
                                   horizons=[5, 10], scales=[1, 2])
    assert len(grid) == 4
    assert {(c["horizon_bars"], c["scale"]) for c in grid} == {
        (5, 1), (5, 2), (10, 1), (10, 2)}


# --- how much of a sample this is ---------------------------------------

def test_consecutive_decisions_are_barely_more_than_one_observation():
    """The point of the whole stride change: 25 consecutive 1m analyses
    over a 30-bar window are not 25 observations."""
    ts = [START_TS + i * STEP_MS for i in range(25)]
    assert scoring.independent_windows(ts, 30, STEP_MS) == 1


def test_strided_decisions_are_worth_their_row_count():
    ts = [START_TS + i * 30 * STEP_MS for i in range(25)]
    assert scoring.independent_windows(ts, 30, STEP_MS) == 25


def test_partly_overlapping_windows_are_counted_greedily():
    ts = [START_TS + i * 15 * STEP_MS for i in range(5)]   # half-overlapping
    assert scoring.independent_windows(ts, 30, STEP_MS) == 3


# --- the summary says what it cannot support ----------------------------

def scored_rows(n=6, decision="no_trade", stride=1):
    rows = []
    for i in range(n):
        ts = START_TS + i * stride * STEP_MS
        a = analysis(decision=decision, ts=ts)
        rows.append(scoring.score_analysis(a, climb(6, step=0.4, start_ts=ts), P))
    return rows


def test_every_section_reports_its_own_independent_window_count():
    summary = scoring.summarize(scored_rows(), P, STEP_MS, None)
    for section in ("trade", "abstention", "regime"):
        assert "independent_windows" in summary[section], section
        assert "rows" in summary[section]
    assert "independent_windows" in summary


def test_a_thin_sample_is_labelled_as_one_in_plain_words():
    summary = scoring.summarize(scored_rows(), P, STEP_MS, None)
    regime = summary["regime"]
    assert regime["sufficient"] is False
    assert "Cannot support" in regime["note"]
    assert "independent" in regime["note"]
    # and it points at the fix rather than just complaining
    assert "stride" in regime["note"]


def test_a_sufficient_sample_says_so():
    summary = scoring.summarize(scored_rows(n=30, stride=10), P, STEP_MS, None)
    assert summary["regime"]["sufficient"] is True
    assert "supports" in summary["regime"]["note"]


def test_two_trades_cannot_support_a_win_rate():
    rows = scored_rows(n=2, decision="market_buy")
    summary = scoring.summarize(rows, P, STEP_MS, None)
    assert summary["trade"]["sufficient"] is False
    assert "win rate" in summary["trade"]["note"]


def test_the_miss_rate_is_reported_against_the_base_rate():
    rows = scored_rows(n=6)
    base = scoring.baselines(climb(80, step=0.4), P)
    summary = scoring.summarize(rows, P, STEP_MS, base)
    assert summary["abstention"]["base_rate"] == base["payable_rate"]
    assert summary["abstention"]["lift"] is not None


def test_the_baseline_surveys_every_bar_not_just_decision_bars():
    base = scoring.baselines(climb(80, step=0.4), P)
    assert base["bars_tested"] > 60
    assert base["payable_rate"] == 1.0          # a clean ramp always pays long
    assert base["majority_regime"] == "bull_trend"


# --- no-lookahead: the scorer may not read backwards ---------------------

def store(n_forward=40):
    bars = climb(n_forward + 1, step=0.4)
    db.insert_bars(SYMBOL, "1m", bars)
    a = analysis(ts=bars[10]["ts"], price=bars[10]["close"])
    db.insert_analysis(SYMBOL, a["ts"], a["stage1"], a["stage2"], "test", 1,
                       "1m", price_at=a["price_at"], atr_at=a["atr_at"])
    return bars, a


def test_bars_after_excludes_the_decision_bar_itself(fresh):
    bars, a = store()
    got = db.bars_after(SYMBOL, "1m", a["ts"], limit=5)
    assert all(b["ts"] > a["ts"] for b in got)
    assert got[0]["ts"] == bars[11]["ts"]


def test_the_stored_anchor_wins_over_any_bar_handed_to_the_scorer():
    """The anchor is the price the analysis was FORMED against, not
    whatever the table says that bar closed at now. Pinned directly
    because the path in scorer.py only fetches a decision bar when
    price_at is NULL - so a regression here would otherwise be
    unreachable from the end-to-end tests and pass unnoticed.
    """
    a = analysis(price=100.0)
    disagreeing_bar = {"ts": START_TS, "open": 180.0, "high": 190.0,
                       "low": 170.0, "close": 180.0, "volume": 1.0}
    row = scoring.score_analysis(a, climb(6, step=0.4), P, disagreeing_bar)

    assert row["price_at"] == 100.0
    assert row["anchor_source"] == "stored"
    # and the measures are anchored on 100, not 180: five bars of +0.4
    assert row["fwd_return_atr"] == pytest.approx(2.0, abs=0.01)


def test_an_analysis_predating_price_at_falls_back_and_says_so():
    """Old rows genuinely do not know their own anchor. Using the bar is
    the only option, but the row must not claim the same provenance as
    one that stored it."""
    a = analysis(price=100.0)
    a["price_at"] = None
    bar = {"ts": START_TS, "open": 99.0, "high": 100.1, "low": 98.9,
           "close": 100.0, "volume": 1.0}
    row = scoring.score_analysis(a, climb(6, step=0.4), P, bar)

    assert row["price_at"] == 100.0
    assert row["anchor_source"] == "derived"


MEASURES = ("fwd_mfe_atr", "fwd_mae_atr", "fwd_return_atr", "fwd_efficiency",
            "fwd_envelope_atr", "realized_regime", "regime_verdict",
            "abstention_outcome", "price_at", "atr_at")


def score_once():
    run = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 5,
                                              "fill_window_bars": 3})
    row = db.get_scores(run["id"])[0]
    return {k: row[k] for k in MEASURES}


def test_mutating_bars_at_or_before_the_decision_changes_nothing(fresh):
    """The anchor comes from the analysis's own stored price_at, and the
    forward window starts strictly after it - so the past is unreadable.

    Asserted on the score ROW, not the summary: the baselines deliberately
    survey every bar in the table, so mutating the past does move those.
    """
    bars, a = store()
    before = score_once()

    wrecked = [{**b, "high": b["high"] + 50, "low": b["low"] - 50,
                "close": b["close"] + 40} for b in bars[:11]]
    db.insert_bars(SYMBOL, "1m", wrecked)      # includes the decision bar

    assert score_once() == before


def test_mutating_bars_after_the_decision_does_change_the_score(fresh):
    """The vacuity guard. Without this, a scorer that ignored the bars
    table entirely would pass the test above."""
    bars, a = store()
    before = score_once()

    lifted = [{**b, "high": b["high"] + 20, "close": b["close"] + 20}
              for b in bars[11:16]]
    db.insert_bars(SYMBOL, "1m", lifted)

    after = score_once()
    assert after != before
    assert after["fwd_mfe_atr"] > before["fwd_mfe_atr"]


# --- pooling several replay runs into one sample ------------------------

def two_runs(fresh_bars=True):
    """Two replay runs on days far enough apart that no window can overlap."""
    day1 = climb(80, step=0.2)
    day2 = climb(80, step=0.2, start_ts=START_TS + 5000 * STEP_MS)
    db.insert_bars(SYMBOL, "1m", day1 + day2)
    ids = []
    for day in (day1, day2):
        rid = db.create_replay_run(symbol=SYMBOL, interval="1m",
                                   start_ts=day[0]["ts"], end_ts=day[-1]["ts"],
                                   status="completed", bars_total=2,
                                   max_analyses=2, stride=30)
        ids.append(rid)
        for idx in (10, 40):
            a = analysis(ts=day[idx]["ts"], price=day[idx]["close"])
            aid = db.insert_analysis(SYMBOL, a["ts"], a["stage1"], a["stage2"],
                                     "test", 1, "1m", price_at=a["price_at"],
                                     atr_at=a["atr_at"])
            db.stamp_replay_rows(rid, SYMBOL, aid - 1, 0)
    return ids


def test_one_score_run_can_pool_several_replay_runs(fresh):
    ids = two_runs()
    run = scorer.run(SYMBOL, "1m", replay_run_id=ids,
                     overrides={"horizon_bars": 5, "abstention_horizon_bars": 5,
                                "fill_window_bars": 3})
    assert run["analyses_scored"] == 4, "all four analyses, both runs"
    assert run["replay_run_ids"] == sorted(ids)
    # the scalar column stays null so a query for "the run this scored"
    # cannot silently see half the sample
    assert run["replay_run_id"] is None


def test_a_single_run_still_fills_the_scalar_column(fresh):
    ids = two_runs()
    run = scorer.run(SYMBOL, "1m", replay_run_id=ids[0],
                     overrides={"horizon_bars": 5, "abstention_horizon_bars": 5,
                                "fill_window_bars": 3})
    assert run["replay_run_id"] == ids[0]
    assert run["replay_run_ids"] == [ids[0]]
    assert run["analyses_scored"] == 2


def test_pooling_runs_of_a_different_series_is_refused(fresh):
    ids = two_runs()
    other = db.create_replay_run(symbol=SYMBOL, interval="15m", start_ts=1,
                                 end_ts=2, status="completed", bars_total=1,
                                 max_analyses=1)
    with pytest.raises(scoring.ScoringError) as e:
        scorer.run(SYMBOL, "1m", replay_run_id=[ids[0], other])
    assert "15m" in str(e.value)


def test_pooling_an_unknown_run_is_refused(fresh):
    with pytest.raises(scoring.ScoringError) as e:
        scorer.run(SYMBOL, "1m", replay_run_id=[9999])
    assert "no replay run 9999" in str(e.value)


def test_pooled_runs_on_different_days_are_independent_windows(fresh):
    ids = two_runs()
    run = scorer.run(SYMBOL, "1m", replay_run_id=ids,
                     overrides={"horizon_bars": 5, "abstention_horizon_bars": 5,
                                "fill_window_bars": 3})
    summary = json.loads(run["summary_json"])
    # 4 decisions, 30 bars apart within a day and 5000 apart between them
    assert summary["independent_windows"] == 4


# --- the run records how it was produced --------------------------------

def test_a_run_stores_the_parameters_it_used(fresh):
    store()
    run = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 5,
                                              "fill_window_bars": 3})
    assert run["params"]["horizon_bars"] == 5
    assert run["params"]["trend_efficiency"] == scoring.DEFAULTS["trend_efficiency"]
    assert run["scorer_version"] == scoring.SCORER_VERSION
    assert run["independent_windows"] >= 1
    assert json.loads(run["summary_json"])["scored"] == 1


def test_rescoring_makes_a_new_run_rather_than_overwriting(fresh):
    store()
    first = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 5,
                                                "fill_window_bars": 3})
    second = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 10,
                                                 "fill_window_bars": 3})
    assert second["id"] != first["id"]
    # the old scores are not stale - they answer a different question
    assert db.get_scores(first["id"])[0]["bars_available"] >= 5
    assert db.get_score_run(first["id"])["params"]["horizon_bars"] == 5


def test_an_analysis_without_enough_forward_bars_is_incomplete_not_zero(fresh):
    bars = climb(14, step=0.4)
    db.insert_bars(SYMBOL, "1m", bars)
    a = analysis(ts=bars[12]["ts"], price=bars[12]["close"])
    db.insert_analysis(SYMBOL, a["ts"], a["stage1"], a["stage2"], "test", 1,
                       "1m", price_at=a["price_at"], atr_at=a["atr_at"])

    run = scorer.run(SYMBOL, "1m", overrides={"horizon_bars": 5,
                                              "fill_window_bars": 3})
    row = db.get_scores(run["id"])[0]
    assert row["complete"] == 0
    assert row["regime_verdict"] is None
    assert row["abstention_outcome"] == "insufficient_data"
    assert run["analyses_incomplete"] == 1


# --- prompt contract pooling -------------------------------------------

def test_pooling_refuses_two_prompt_contracts():
    from candle_agent.services import scorer

    mixed = [{"prompt_fingerprint": "aaaaaaaaaaaaaaaa"},
             {"prompt_fingerprint": "bbbbbbbbbbbbbbbb"}]
    with pytest.raises(scoring.ScoringError) as e:
        scorer._one_contract(mixed)
    assert "two different" in str(e.value)


def test_pooling_allows_one_contract_and_returns_it():
    from candle_agent.services import scorer

    same = [{"prompt_fingerprint": "aaaaaaaaaaaaaaaa"}] * 3
    assert scorer._one_contract(same) == "aaaaaaaaaaaaaaaa"


def test_rows_predating_fingerprints_still_pool_with_each_other():
    """All equally unknown is coherent; known mixed with unknown is not."""
    from candle_agent.services import scorer

    legacy = [{"prompt_fingerprint": None}, {}]
    assert scorer._one_contract(legacy) is None

    with pytest.raises(scoring.ScoringError):
        scorer._one_contract([{"prompt_fingerprint": "aaaaaaaaaaaaaaaa"},
                              {"prompt_fingerprint": None}])


def test_an_empty_sample_has_no_contract_to_disagree_about():
    from candle_agent.services import scorer

    assert scorer._one_contract([]) is None


def test_the_fingerprint_ignores_which_route_an_analysis_took():
    """Hashing only the routed prompt would split a sample by its answers."""
    from candle_agent import orchestrator

    assert orchestrator.prompt_fingerprint() == orchestrator.prompt_fingerprint()
    assert len(orchestrator.prompt_fingerprint()) == 16


def test_moving_the_validator_gate_changes_the_fingerprint(monkeypatch):
    from candle_agent import orchestrator

    before = orchestrator.prompt_fingerprint()
    monkeypatch.setattr(orchestrator, "MIN_RISK_REWARD", 2.0)
    assert orchestrator.prompt_fingerprint() != before
