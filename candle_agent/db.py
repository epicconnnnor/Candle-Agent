"""SQLite storage for bars, analyses, paper trades and replay runs."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

DEFAULT_DB_PATH = "candle_agent.db"
DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def db_path():
    """Resolved per connection, not captured at import.

    Services set DB_PATH before anything imports this module, so either
    would do for them - but tests set it per module, and a value frozen at
    import time would silently point every test file at one shared file.
    """
    return os.environ.get("DB_PATH", DB_PATH)

# Bars are keyed by (symbol, interval, ts): the same symbol at 1m and 5m
# are different series, and without interval in the key they would
# overwrite each other row for row.
BARS_DDL = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    -- which feed produced this row: 'alpaca', 'binance', 'demo'. The demo
    -- generator writes here under real symbol names, so without this a
    -- synthetic bar is indistinguishable from a real one until somebody
    -- notices the price is wrong.
    source TEXT,
    PRIMARY KEY (symbol, interval, ts)
);
"""

SCHEMA = BARS_DDL + """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    stage1 TEXT NOT NULL,
    stage2 TEXT NOT NULL,
    model TEXT,
    latency_ms INTEGER,
    interval TEXT NOT NULL DEFAULT '1m',
    -- the market this verdict was formed against; nullable because rows
    -- written before these existed genuinely do not know
    price_at REAL,
    atr_at REAL,
    -- null for live analyses; set for rows produced by a replay run
    replay_run_id INTEGER,
    -- measured from the provider's usage block, so cost estimates come
    -- from history rather than a character heuristic
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    -- identity of the prompt/schema/validator contract this verdict was
    -- formed under. Two analyses are only comparable when these match.
    prompt_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT, order_type TEXT, status TEXT,
    entry REAL, stop REAL, target REAL,
    created_ts INTEGER, filled_ts INTEGER, closed_ts INTEGER,
    exit_price REAL, exit_reason TEXT, r_multiple REAL,
    bars_pending INTEGER DEFAULT 0,
    -- separated at write time so scoring never has to reconstruct which
    -- trades came from which replay
    replay_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS replay_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    status TEXT NOT NULL,
    bars_total INTEGER NOT NULL DEFAULT 0,
    bars_done INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    created_at INTEGER NOT NULL,
    -- cost control: a run cannot start without an explicit cap
    max_analyses INTEGER NOT NULL,
    analyses_done INTEGER NOT NULL DEFAULT 0,
    estimated_tokens INTEGER,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    -- publish every Nth bar; 1 = every bar. Recorded per run because it
    -- decides how much the scored forward windows overlap, and therefore
    -- how many independent observations the run is actually worth.
    stride INTEGER NOT NULL DEFAULT 1,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS score_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replay_run_id INTEGER,              -- null: live analyses can be scored too
    -- JSON array when a score run spans several replay runs. replay_run_id
    -- above stays populated for the single-run case so existing queries
    -- keep working; it is null when this names more than one.
    replay_run_ids TEXT,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    -- every threshold in the scoring layer is a judgement call, so the
    -- parameters travel with the scores. Without them a stored score
    -- cannot be interpreted, only misread.
    scorer_version TEXT NOT NULL,
    params_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    analyses_scored INTEGER NOT NULL DEFAULT 0,
    analyses_incomplete INTEGER NOT NULL DEFAULT 0,
    independent_windows INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    -- the contract every scored analysis shared; a run that could not
    -- agree on one is refused rather than stored
    prompt_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS analysis_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    score_run_id INTEGER NOT NULL,
    analysis_id INTEGER NOT NULL,
    symbol TEXT NOT NULL, interval TEXT NOT NULL, bar_ts INTEGER NOT NULL,

    -- denormalised on purpose: a score row must stay reproducible on its
    -- own, and must not silently change if the analyses row is amended
    price_at REAL, atr_at REAL, anchor_source TEXT,
    bars_available INTEGER, window_end_ts INTEGER,
    complete INTEGER NOT NULL DEFAULT 0,

    -- continuous measures, stored so every threshold can be re-swept
    -- later without re-running anything. Never store only the label.
    fwd_mfe_atr REAL, fwd_mae_atr REAL, fwd_return_atr REAL,
    fwd_efficiency REAL, fwd_envelope_atr REAL, horizons_json TEXT,

    claimed_regime TEXT, claimed_strength TEXT,
    decision TEXT, confidence TEXT,
    entry REAL, stop REAL, target REAL,
    distance_to_nearest_level_atr REAL,

    trade_outcome TEXT, filled_ts INTEGER, bars_to_fill INTEGER,
    exit_ts INTEGER, bars_to_exit INTEGER,
    r_multiple REAL, mtm_r REAL, trade_mae_r REAL, trade_mfe_r REAL,
    entry_distance_atr REAL, same_bar_ambiguous INTEGER DEFAULT 0,

    abstention_outcome TEXT, missed_direction TEXT,
    miss_aligned INTEGER, bars_to_payoff INTEGER,

    realized_regime TEXT, regime_verdict TEXT,

    UNIQUE (score_run_id, analysis_id)
);
"""

_BAR_COLS = "symbol, interval, ts, open, high, low, close, volume"

# The one `source` value that is not market data. Everything else names a
# real venue, so "is this synthetic" is a single comparison rather than a
# guess about prices or timestamps.
SYNTHETIC = "demo"

# NULL means "written before this column existed", NOT "synthetic". Such
# rows read as real: refusing to show them would blank every database
# predating this column, and a row's true provenance is a data question,
# not a schema one - see scripts/backfill_bar_source.py.
_REAL_ONLY = f"(source IS NULL OR source <> '{SYNTHETIC}')"


def _real_only(include_synthetic):
    """SQL fragment restricting a bars query to real market data.

    `include_synthetic=None` means "decide from the environment": demo
    mode is allowed to see its own bars, because otherwise the demo path
    would write a series it could never read back. Anything else gets
    real data only, which is what makes a stray demo run visible as
    missing bars rather than as a plausible wrong price.

    Read per call rather than at import for the same reason as db_path():
    tests set INGEST_MODE per module.
    """
    if include_synthetic is None:
        include_synthetic = os.environ.get("INGEST_MODE", "").lower() == SYNTHETIC
    return "" if include_synthetic else f" AND {_REAL_ONLY}"


def _columns(c, table):
    return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def _migrate(c):
    """Bring a pre-interval database up to the current shape.

    Runs before the CREATE TABLE statements, so a fresh database skips it
    entirely (no table -> no columns -> nothing to do).
    """
    bar_cols = _columns(c, "bars")
    if bar_cols and "interval" not in bar_cols:
        # SQLite cannot add a column to a PRIMARY KEY, so the table has to
        # be rebuilt. Pre-migration rows can only have been 1m: that was
        # the single hardcoded interval.
        c.executescript(
            "ALTER TABLE bars RENAME TO bars_pre_interval;"
            + BARS_DDL
            + f"INSERT OR IGNORE INTO bars ({_BAR_COLS}) "
              "SELECT symbol, '1m', ts, open, high, low, close, volume "
              "FROM bars_pre_interval;"
              "DROP TABLE bars_pre_interval;"
        )
        print("[db] migrated bars to (symbol, interval, ts)")

    # re-read: the interval rebuild above replaces the table wholesale, and
    # the new DDL already carries `source`.
    bar_cols = _columns(c, "bars")
    if bar_cols and "source" not in bar_cols:
        # No DEFAULT, for exactly the reason price_at has none below: a row
        # written before provenance was recorded genuinely does not know
        # where it came from. Stamping them all 'real' would certify the
        # very demo bars this column exists to expose.
        c.execute("ALTER TABLE bars ADD COLUMN source TEXT")
        print("[db] migrated bars: added source")

    analysis_cols = _columns(c, "analyses")
    if analysis_cols and "interval" not in analysis_cols:
        c.execute("ALTER TABLE analyses ADD COLUMN interval TEXT NOT NULL DEFAULT '1m'")
        print("[db] migrated analyses: added interval")
    # No DEFAULT: a pre-existing row must read NULL, not a fabricated price.
    # The UI shows those as "age unknown" rather than claiming freshness.
    for column in ("price_at", "atr_at"):
        if analysis_cols and column not in analysis_cols:
            c.execute(f"ALTER TABLE analyses ADD COLUMN {column} REAL")
            print(f"[db] migrated analyses: added {column}")
    for column in ("replay_run_id", "prompt_tokens", "completion_tokens"):
        if analysis_cols and column not in analysis_cols:
            c.execute(f"ALTER TABLE analyses ADD COLUMN {column} INTEGER")
            print(f"[db] migrated analyses: added {column}")

    # Nullable for the same reason as price_at: rows written before the
    # contract was recorded genuinely do not know which one they ran under,
    # and inventing one would let them pool with rows that are not comparable.
    if analysis_cols and "prompt_fingerprint" not in analysis_cols:
        c.execute("ALTER TABLE analyses ADD COLUMN prompt_fingerprint TEXT")
        print("[db] migrated analyses: added prompt_fingerprint")

    # DEFAULT 1 is honest here, unlike price_at above: every run that
    # predates this column really did publish every bar.
    score_cols = _columns(c, "score_runs")
    if score_cols and "replay_run_ids" not in score_cols:
        c.execute("ALTER TABLE score_runs ADD COLUMN replay_run_ids TEXT")
        print("[db] migrated score_runs: added replay_run_ids")

    if score_cols and "prompt_fingerprint" not in score_cols:
        c.execute("ALTER TABLE score_runs ADD COLUMN prompt_fingerprint TEXT")
        print("[db] migrated score_runs: added prompt_fingerprint")

    run_cols = _columns(c, "replay_runs")
    if run_cols and "stride" not in run_cols:
        c.execute("ALTER TABLE replay_runs ADD COLUMN stride INTEGER NOT NULL DEFAULT 1")
        print("[db] migrated replay_runs: added stride")

    trade_cols = _columns(c, "paper_trades")
    if trade_cols and "replay_run_id" not in trade_cols:
        c.execute("ALTER TABLE paper_trades ADD COLUMN replay_run_id INTEGER")
        print("[db] migrated paper_trades: added replay_run_id")


@contextmanager
def conn():
    # WAL + busy_timeout: several service processes (ingest, analyzer, api)
    # share this file concurrently. WAL allows readers during a write;
    # busy_timeout makes writers wait instead of failing on a locked db.
    c = sqlite3.connect(db_path(), timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        _migrate(c)
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def insert_bar(symbol, interval, ts, o, h, l, cl, v, source=None):
    with conn() as c:
        c.execute(
            f"INSERT OR REPLACE INTO bars ({_BAR_COLS}, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (symbol, interval, ts, o, h, l, cl, v, source),
        )


def insert_bars(symbol, interval, bars, source=None):
    """Bulk insert (history backfill). Existing rows are overwritten.

    `source` is the feed's own name, so a caller that already holds a
    source object passes `source.name` and cannot get it wrong - demo mode
    resolves to the demo source and therefore stamps 'demo' by itself.
    """
    with conn() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO bars ({_BAR_COLS}, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(symbol, interval, b["ts"], b["open"], b["high"],
              b["low"], b["close"], b["volume"], source) for b in bars],
        )


def delete_bars(symbol, interval=None, source=None) -> int:
    """Drop stored bars for a symbol, optionally one interval or source.

    Bars are derived data - re-fetchable from the source - so clearing and
    refilling is the safe way to evict a contaminated series. Always
    scoped to a symbol; there is deliberately no "delete everything".

    `source=SYNTHETIC` is the cheap version of the segment hunt that used
    to be necessary: once provenance is recorded, evicting demo bars is a
    fact about the row rather than an inference from its timestamp.
    """
    if not symbol:
        raise ValueError("delete_bars requires a symbol")
    sql = "DELETE FROM bars WHERE symbol=?"
    params = [symbol]
    if interval:
        sql += " AND interval=?"
        params.append(interval)
    if source:
        sql += " AND source=?"
        params.append(source)
    with conn() as c:
        return c.execute(sql, params).rowcount


def delete_bars_range(symbol, interval, start_ts, end_ts) -> int:
    """Drop stored bars for a symbol inside an inclusive timestamp window.

    The narrow sibling of `delete_bars`, for when only part of a series is
    contaminated and the rest is worth keeping. The window is the whole
    argument for the deletion - a segment identified by its timestamps -
    so both ends are required and both are inclusive. Callers name a time
    range they can defend, never a price range: a bad segment is
    recognised by when it arrived, not by what it cost.
    """
    if not symbol or not interval:
        raise ValueError("delete_bars_range requires a symbol and an interval")
    if start_ts > end_ts:
        raise ValueError("start_ts must not be after end_ts")
    with conn() as c:
        return c.execute(
            "DELETE FROM bars WHERE symbol=? AND interval=? AND ts BETWEEN ? AND ?",
            (symbol, interval, start_ts, end_ts),
        ).rowcount


def active_interval(symbol, include_synthetic=None):
    """The interval of the newest stored bar for a symbol, if any.

    Lets callers that do not care about intervals - the analyzer - keep
    asking for "this symbol's bars" and get a single coherent series.

    Filtered like every other reader: a stray demo bar must not be able to
    decide which interval the rest of the system then works in.
    """
    with conn() as c:
        r = c.execute(
            "SELECT interval FROM bars WHERE symbol=?"
            f"{_real_only(include_synthetic)} ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return r["interval"] if r else None


def recent_bars(symbol, limit=100, interval=None, as_of_ts=None,
                include_synthetic=None):
    """Newest `limit` bars at or before `as_of_ts`, oldest first.

    `interval=None` means the most recently ingested interval for this
    symbol, so a series is never a mix of granularities.

    `as_of_ts` is the no-lookahead bound. It matters for replay, where the
    whole history is already stored, but it is not replay-specific: on the
    live path an analysis of bar N could previously read bars newer than N
    whenever the analyzer lagged ingest or a message was redelivered.
    """
    interval = interval or active_interval(symbol, include_synthetic)
    if interval is None:
        return []

    where = "symbol=? AND interval=?" + _real_only(include_synthetic)
    params = [symbol, interval]
    if as_of_ts:
        where += " AND ts <= ?"
        params.append(as_of_ts)
    params.append(limit)

    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM bars WHERE {where} ORDER BY ts DESC LIMIT ?", params
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def count_bars_before(symbol, interval, ts, include_synthetic=None) -> int:
    """How much history precedes a bar - the analyzer needs MIN_BARS of it."""
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM bars WHERE symbol=? AND interval=? AND ts < ?"
            + _real_only(include_synthetic),
            (symbol, interval, ts)).fetchone()[0]


def bars_in_range(symbol, interval, start_ts, end_ts, include_synthetic=None):
    """Every stored bar in a window, oldest first. The replay source.

    Filtered too: a replay is evidence about the model, and it is worth
    nothing if the series underneath was partly invented.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bars WHERE symbol=? AND interval=? AND ts BETWEEN ? AND ?"
            + _real_only(include_synthetic)
            + " ORDER BY ts ASC", (symbol, interval, start_ts, end_ts)).fetchall()
    return [dict(r) for r in rows]


def insert_analysis(symbol, ts, stage1, stage2, model, latency_ms, interval="1m",
                    price_at=None, atr_at=None,
                    prompt_tokens=None, completion_tokens=None,
                    prompt_fingerprint=None):
    """`price_at` / `atr_at` capture the market at the moment of analysis, so
    staleness can be judged later without guessing.

    `prompt_fingerprint` does the same for the question rather than the
    market: it records which prompt and schema contract produced this
    verdict, so a later score run can tell whether two rows are answers to
    the same question or to two different ones.
    """
    with conn() as c:
        cur = c.execute(
            "INSERT INTO analyses (symbol, ts, stage1, stage2, model, latency_ms, "
            "interval, price_at, atr_at, prompt_tokens, completion_tokens, "
            "prompt_fingerprint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, ts, json.dumps(stage1), json.dumps(stage2), model, latency_ms,
             interval, price_at, atr_at, prompt_tokens, completion_tokens,
             prompt_fingerprint),
        )
        return cur.lastrowid


def latest_analysis(symbol, interval=None):
    where, params = "symbol=?", [symbol]
    if interval:
        where, params = "symbol=? AND interval=?", [symbol, interval]
    with conn() as c:
        r = c.execute(
            f"SELECT * FROM analyses WHERE {where} ORDER BY id DESC LIMIT 1", params
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["stage1"] = json.loads(d["stage1"])
    d["stage2"] = json.loads(d["stage2"])
    return d


# --- replay runs ---

_RUN_COLS = ("symbol", "interval", "start_ts", "end_ts", "status", "bars_total",
             "bars_done", "model", "created_at", "max_analyses", "analyses_done",
             "estimated_tokens", "stop_requested", "stride", "detail")


def create_replay_run(**fields) -> int:
    row = {k: fields.get(k) for k in _RUN_COLS}
    row["created_at"] = row["created_at"] or int(time.time() * 1000)
    row["status"] = row["status"] or "pending"
    for k in ("bars_done", "analyses_done", "stop_requested"):
        row[k] = row[k] or 0
    row["stride"] = row["stride"] or 1
    cols = ", ".join(_RUN_COLS)
    marks = ", ".join("?" * len(_RUN_COLS))
    with conn() as c:
        cur = c.execute(f"INSERT INTO replay_runs ({cols}) VALUES ({marks})",
                        [row[k] for k in _RUN_COLS])
        return cur.lastrowid


def get_replay_run(run_id: int):
    with conn() as c:
        r = c.execute("SELECT * FROM replay_runs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def update_replay_run(run_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in _RUN_COLS}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    with conn() as c:
        c.execute(f"UPDATE replay_runs SET {sets} WHERE id=?",
                  [*allowed.values(), run_id])


def request_replay_stop(run_id: int) -> bool:
    with conn() as c:
        return c.execute(
            "UPDATE replay_runs SET stop_requested=1 WHERE id=? AND status IN "
            "('pending','running')", (run_id,)).rowcount > 0


def stamp_replay_rows(run_id: int, symbol: str,
                      analysis_id_floor: int, trade_id_floor: int) -> tuple[int, int]:
    """Attribute rows written since the run started to that run.

    Done here rather than by the analyzer: the analyzer must not be able to
    tell replay from live, so it cannot write the id itself. Safe because
    replay refuses to run while live ingest is streaming the same symbol,
    and the id floors exclude anything that predates the run.
    """
    with conn() as c:
        a = c.execute(
            "UPDATE analyses SET replay_run_id=? WHERE symbol=? AND id>? "
            "AND replay_run_id IS NULL", (run_id, symbol, analysis_id_floor)).rowcount
        t = c.execute(
            "UPDATE paper_trades SET replay_run_id=? WHERE symbol=? AND id>? "
            "AND replay_run_id IS NULL", (run_id, symbol, trade_id_floor)).rowcount
    return a, t


def max_id(table: str) -> int:
    if table not in ("analyses", "paper_trades"):
        raise ValueError(f"unsupported table {table!r}")
    with conn() as c:
        return c.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]


def token_stats(model: str | None = None) -> dict:
    """Measured tokens per analysis, for costing a run before it starts."""
    where, params = "prompt_tokens IS NOT NULL", []
    if model:
        where += " AND model=?"
        params.append(model)
    with conn() as c:
        r = c.execute(
            f"SELECT COUNT(*), AVG(prompt_tokens), AVG(completion_tokens) "
            f"FROM analyses WHERE {where}", params).fetchone()
    return {"samples": r[0], "avg_prompt": r[1], "avg_completion": r[2]}


# --- scoring ---

_SCORE_RUN_COLS = ("replay_run_id", "replay_run_ids", "symbol", "interval",
                   "scorer_version",
                   "params_json", "created_at", "analyses_scored",
                   "analyses_incomplete", "independent_windows", "summary_json",
                   "status", "detail", "prompt_fingerprint")

_SCORE_COLS = (
    "score_run_id", "analysis_id", "symbol", "interval", "bar_ts",
    "price_at", "atr_at", "anchor_source", "bars_available", "window_end_ts",
    "complete", "fwd_mfe_atr", "fwd_mae_atr", "fwd_return_atr",
    "fwd_efficiency", "fwd_envelope_atr", "horizons_json",
    "claimed_regime", "claimed_strength", "decision", "confidence",
    "entry", "stop", "target", "distance_to_nearest_level_atr",
    "trade_outcome", "filled_ts", "bars_to_fill", "exit_ts", "bars_to_exit",
    "r_multiple", "mtm_r", "trade_mae_r", "trade_mfe_r", "entry_distance_atr",
    "same_bar_ambiguous", "abstention_outcome", "missed_direction",
    "miss_aligned", "bars_to_payoff", "realized_regime", "regime_verdict",
)


def bars_after(symbol, interval, ts, limit, include_synthetic=None):
    """Bars STRICTLY after ts, oldest first.

    The scorer's only view of the future, and the mirror of recent_bars'
    as-of bound: that one may not look forward, this one may not look
    back. `>` not `>=` - the decision bar itself is history.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bars WHERE symbol=? AND interval=? AND ts > ?"
            + _real_only(include_synthetic)
            + " ORDER BY ts ASC LIMIT ?", (symbol, interval, ts, limit)).fetchall()
    return [dict(r) for r in rows]


def analyses_for_scoring(symbol, interval=None, replay_run_ids=None,
                         start_ts=None, end_ts=None):
    """Stored analyses, oldest first, with their JSON parsed.

    `replay_run_ids` may be a single id or several. Several is how a
    sample is accumulated across runs: one replay of a single session is
    rarely enough rows, and runs over different days produce windows that
    cannot overlap.
    """
    where, params = ["symbol=?"], [symbol]
    if interval:
        where.append("interval=?")
        params.append(interval)
    if replay_run_ids is not None:
        ids = [replay_run_ids] if isinstance(replay_run_ids, int) else list(replay_run_ids)
        where.append("replay_run_id IN (%s)" % ",".join("?" * len(ids)))
        params.extend(ids)
    if start_ts is not None:
        where.append("ts >= ?")
        params.append(start_ts)
    if end_ts is not None:
        where.append("ts <= ?")
        params.append(end_ts)
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM analyses WHERE {' AND '.join(where)} ORDER BY ts ASC",
            params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["stage1"] = json.loads(d["stage1"])
        d["stage2"] = json.loads(d["stage2"])
        out.append(d)
    return out


def create_score_run(**fields) -> int:
    row = {k: fields.get(k) for k in _SCORE_RUN_COLS}
    row["created_at"] = row["created_at"] or int(time.time() * 1000)
    row["status"] = row["status"] or "running"
    for k in ("analyses_scored", "analyses_incomplete", "independent_windows"):
        row[k] = row[k] or 0
    cols = ", ".join(_SCORE_RUN_COLS)
    marks = ", ".join("?" * len(_SCORE_RUN_COLS))
    with conn() as c:
        cur = c.execute(f"INSERT INTO score_runs ({cols}) VALUES ({marks})",
                        [row[k] for k in _SCORE_RUN_COLS])
        return cur.lastrowid


def update_score_run(run_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in _SCORE_RUN_COLS}
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)
    with conn() as c:
        c.execute(f"UPDATE score_runs SET {sets} WHERE id=?",
                  [*allowed.values(), run_id])


def get_score_run(run_id: int):
    with conn() as c:
        r = c.execute("SELECT * FROM score_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
    d["summary"] = json.loads(d["summary_json"]) if d["summary_json"] else None
    d["replay_run_ids"] = json.loads(d["replay_run_ids"]) if d["replay_run_ids"] else (
        [d["replay_run_id"]] if d["replay_run_id"] is not None else [])
    return d


def insert_scores(score_run_id: int, rows: list[dict]) -> int:
    """Bulk insert. Re-scoring makes a NEW run rather than overwriting an
    old one: the parameters differ, so the old scores are not stale, they
    are answers to a different question."""
    cols = ", ".join(_SCORE_COLS)
    marks = ", ".join("?" * len(_SCORE_COLS))
    payload = []
    for row in rows:
        r = {**row, "score_run_id": score_run_id}
        r["horizons_json"] = json.dumps(r.get("horizons_json") or {})
        payload.append([r.get(k) for k in _SCORE_COLS])
    with conn() as c:
        c.executemany(f"INSERT INTO analysis_scores ({cols}) VALUES ({marks})",
                      payload)
    return len(payload)


def get_scores(score_run_id: int):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM analysis_scores WHERE score_run_id=? ORDER BY bar_ts ASC",
            (score_run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["horizons_json"] = json.loads(d["horizons_json"]) if d["horizons_json"] else {}
        out.append(d)
    return out


# --- paper trading ---

_TRADE_COLS = ("symbol", "direction", "order_type", "status", "entry", "stop",
               "target", "created_ts", "filled_ts", "closed_ts", "exit_price",
               "exit_reason", "r_multiple", "bars_pending", "replay_run_id")


def save_trade(trade: dict) -> int:
    """Insert or update (by id). Returns the row id."""
    with conn() as c:
        if trade.get("id"):
            sets = ", ".join(f"{k}=?" for k in _TRADE_COLS)
            c.execute(f"UPDATE paper_trades SET {sets} WHERE id=?",
                      [trade.get(k) for k in _TRADE_COLS] + [trade["id"]])
            return trade["id"]
        cols = ", ".join(_TRADE_COLS)
        marks = ", ".join("?" * len(_TRADE_COLS))
        cur = c.execute(f"INSERT INTO paper_trades ({cols}) VALUES ({marks})",
                        [trade.get(k) for k in _TRADE_COLS])
        return cur.lastrowid


def active_trade(symbol: str):
    """The pending/open trade for a symbol, if any (crash recovery)."""
    with conn() as c:
        r = c.execute(
            "SELECT * FROM paper_trades WHERE symbol=? AND status IN ('pending','open') "
            "ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    return dict(r) if r else None


def active_trades():
    """Every pending/open trade, newest first per symbol.

    Crash recovery cannot assume it knows which symbol was being traded,
    so it reloads all of them.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM paper_trades WHERE status IN ('pending','open') "
            "ORDER BY id DESC").fetchall()
    seen, out = set(), []
    for r in rows:
        d = dict(r)
        if d["symbol"] in seen:
            continue
        seen.add(d["symbol"])
        out.append(d)
    return out


def trade_history(symbol: str, limit: int = 50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM paper_trades WHERE symbol=? AND status NOT IN ('pending','open') "
            "ORDER BY id DESC LIMIT ?", (symbol, limit)).fetchall()
    return [dict(r) for r in rows]
