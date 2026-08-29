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
    hlc = []
    price = start
    for _ in range(n):
        nxt = price + step
        hlc.append((nxt + 0.05, price - 0.05, nxt))
        price = nxt
    return series(hlc, start_ts, open0=start)


P = scoring.resolve_params({"horizon_bars": 5, "fill_window_bars": 3})


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
