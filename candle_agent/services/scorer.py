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


def run(symbol: str, interval: str, replay_run_id: int | None = None,
        overrides: dict | None = None, start_ts: int | None = None,
        end_ts: int | None = None) -> dict:
    """Score every stored analysis in scope. Returns the run row."""
    symbol = symbol.upper()
    params = scoring.resolve_params(overrides)
    horizon = int(params["horizon_bars"])
    reach = horizon + int(params["fill_window_bars"])

    interval_ms = to_ms(interval)
    analyses = db.analyses_for_scoring(symbol, interval, replay_run_id,
                                       start_ts, end_ts)

    run_id = db.create_score_run(
        replay_run_id=replay_run_id, symbol=symbol, interval=interval,
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
