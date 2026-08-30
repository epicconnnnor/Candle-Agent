"""The I/O shell around candle_agent.scoring.

Deliberately NOT a long-running service and deliberately not wired to
replay completion. Scoring is pure arithmetic over bars already stored -
no LLM, no cost, deterministic - so it runs on demand, synchronously,
and can be re-run with different parameters as often as anyone likes.
Re-scoring is the common case; coupling it to replay would buy nothing
and would make the parameters feel fixed when they are the whole point.

Same split as paper.py / services/paper_trader.py: the arithmetic is pure
and lives next door, this module owns the database.

There is no main(). If one appears here, something has gone wrong.
"""
import json
import time

from .. import db, scoring
from ..intervals import to_ms


def _run_ids(replay_run_id, symbol: str, interval: str) -> list[int] | None:
    """Normalise and check the replay runs a score run is allowed to pool."""
    if replay_run_id is None:
        return None
    ids = ([replay_run_id] if isinstance(replay_run_id, int)
           else [int(x) for x in replay_run_id])
    if not ids:
        return None
    for rid in ids:
        row = db.get_replay_run(rid)
        if row is None:
            raise scoring.ScoringError(f"no replay run {rid}")
        if row["symbol"] != symbol or row["interval"] != interval:
            raise scoring.ScoringError(
                f"replay run {rid} is {row['symbol']} {row['interval']}, not "
                f"{symbol} {interval}: pooling them would mix two different "
                "questions into one sample")
    return sorted(set(ids))


def run(symbol: str, interval: str, replay_run_id=None,
        overrides: dict | None = None, start_ts: int | None = None,
        end_ts: int | None = None) -> dict:
    """Score every stored analysis in scope. Returns the run row.

    `replay_run_id` may name one replay run or several. Several is how a
    sample is accumulated: a single session rarely yields enough rows to
    clear the gates, and runs over different days produce windows that
    cannot overlap. The runs must agree on symbol and interval - scoring
    a 1m run beside a 15m one would pool two different questions.
    """
    symbol = symbol.upper()
    ids = _run_ids(replay_run_id, symbol, interval)
    params = scoring.resolve_params(overrides)
    # Far enough for whichever grader looks furthest: the regime window,
    # the abstention window, or a trade that fills late and then needs its
    # full horizon. Deriving this from horizon_bars alone silently starved
    # the abstention grader once the two horizons were decoupled.
    reach = (max(int(params["horizon_bars"]), int(params["abstention_horizon_bars"]))
             + int(params["fill_window_bars"]))

    interval_ms = to_ms(interval)
    analyses = db.analyses_for_scoring(symbol, interval, ids, start_ts, end_ts)

    run_id = db.create_score_run(
        # the scalar column stays populated only when there is exactly one,
        # so a query for "the run this scored" cannot silently see half a sample
        replay_run_id=ids[0] if ids and len(ids) == 1 else None,
        replay_run_ids=json.dumps(ids) if ids else None,
        symbol=symbol, interval=interval,
        scorer_version=scoring.SCORER_VERSION,
        # the parameters travel with the scores; without them a stored
        # score cannot be interpreted, only misread
        params_json=json.dumps({k: list(v) if isinstance(v, tuple) else v
                                for k, v in params.items()}),
        created_at=int(time.time() * 1000), status="running")

    if not analyses:
        db.update_score_run(run_id, status="empty",
                            detail=f"no stored analyses for {symbol} {interval}")
        return db.get_score_run(run_id)

    rows = []
    for analysis in analyses:
        # STRICTLY after: the decision bar itself is history, and nothing
        # at or before it may be re-read here.
        forward = scoring.contiguous_prefix(
            db.bars_after(symbol, interval, analysis["ts"], limit=reach), interval_ms)
        decision_bar = None
        if analysis.get("price_at") is None:
            window = db.recent_bars(symbol, limit=1, interval=interval,
                                    as_of_ts=analysis["ts"])
            decision_bar = window[-1] if window else None
        rows.append(scoring.score_analysis(analysis, forward, params, decision_bar))

    db.insert_scores(run_id, rows)

    series = db.bars_in_range(symbol, interval, 0, 2 ** 62)
    base = scoring.baselines(series, params, interval_ms)
    summary = scoring.summarize(rows, params, interval_ms, base)

    db.update_score_run(
        run_id, status="completed",
        analyses_scored=summary["scored"],
        analyses_incomplete=summary["incomplete"],
        independent_windows=summary["independent_windows"],
        summary_json=json.dumps(summary))
    return db.get_score_run(run_id)
