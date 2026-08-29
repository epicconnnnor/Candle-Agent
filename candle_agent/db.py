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
    completion_tokens INTEGER
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
"""

_BAR_COLS = "symbol, interval, ts, open, high, low, close, volume"


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

    # DEFAULT 1 is honest here, unlike price_at above: every run that
    # predates this column really did publish every bar.
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


def insert_bar(symbol, interval, ts, o, h, l, cl, v):
    with conn() as c:
        c.execute(
            f"INSERT OR REPLACE INTO bars ({_BAR_COLS}) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, interval, ts, o, h, l, cl, v),
        )


def insert_bars(symbol, interval, bars):
    """Bulk insert (history backfill). Existing rows are overwritten."""
    with conn() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO bars ({_BAR_COLS}) VALUES (?,?,?,?,?,?,?,?)",
            [(symbol, interval, b["ts"], b["open"], b["high"],
              b["low"], b["close"], b["volume"]) for b in bars],
        )


def delete_bars(symbol, interval=None) -> int:
    """Drop stored bars for a symbol, optionally one interval only.

    Bars are derived data - re-fetchable from the source - so clearing and
    refilling is the safe way to evict a contaminated series. Always
    scoped to a symbol; there is deliberately no "delete everything".
    """
    if not symbol:
        raise ValueError("delete_bars requires a symbol")
    sql = "DELETE FROM bars WHERE symbol=?"
    params = [symbol]
    if interval:
        sql += " AND interval=?"
        params.append(interval)
    with conn() as c:
        return c.execute(sql, params).rowcount


def active_interval(symbol):
    """The interval of the newest stored bar for a symbol, if any.

    Lets callers that do not care about intervals - the analyzer - keep
    asking for "this symbol's bars" and get a single coherent series.
    """
    with conn() as c:
        r = c.execute(
            "SELECT interval FROM bars WHERE symbol=? ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return r["interval"] if r else None


def recent_bars(symbol, limit=100, interval=None, as_of_ts=None):
    """Newest `limit` bars at or before `as_of_ts`, oldest first.

    `interval=None` means the most recently ingested interval for this
    symbol, so a series is never a mix of granularities.

    `as_of_ts` is the no-lookahead bound. It matters for replay, where the
    whole history is already stored, but it is not replay-specific: on the
    live path an analysis of bar N could previously read bars newer than N
    whenever the analyzer lagged ingest or a message was redelivered.
    """
    interval = interval or active_interval(symbol)
    if interval is None:
        return []

    where = "symbol=? AND interval=?"
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


def count_bars_before(symbol, interval, ts) -> int:
    """How much history precedes a bar - the analyzer needs MIN_BARS of it."""
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM bars WHERE symbol=? AND interval=? AND ts < ?",
            (symbol, interval, ts)).fetchone()[0]


def bars_in_range(symbol, interval, start_ts, end_ts):
    """Every stored bar in a window, oldest first. The replay source."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bars WHERE symbol=? AND interval=? AND ts BETWEEN ? AND ? "
            "ORDER BY ts ASC", (symbol, interval, start_ts, end_ts)).fetchall()
    return [dict(r) for r in rows]


def insert_analysis(symbol, ts, stage1, stage2, model, latency_ms, interval="1m",
                    price_at=None, atr_at=None,
                    prompt_tokens=None, completion_tokens=None):
    """`price_at` / `atr_at` capture the market at the moment of analysis, so
    staleness can be judged later without guessing."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO analyses (symbol, ts, stage1, stage2, model, latency_ms, "
            "interval, price_at, atr_at, prompt_tokens, completion_tokens) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, ts, json.dumps(stage1), json.dumps(stage2), model, latency_ms,
             interval, price_at, atr_at, prompt_tokens, completion_tokens),
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
