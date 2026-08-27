"""SQLite storage for bars and analyses."""
import json
import os
import sqlite3
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
    atr_at REAL
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT, order_type TEXT, status TEXT,
    entry REAL, stop REAL, target REAL,
    created_ts INTEGER, filled_ts INTEGER, closed_ts INTEGER,
    exit_price REAL, exit_reason TEXT, r_multiple REAL,
    bars_pending INTEGER DEFAULT 0
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


def recent_bars(symbol, limit=100, interval=None):
    """Newest `limit` bars, oldest first.

    `interval=None` means the most recently ingested interval for this
    symbol, so a series is never a mix of granularities.
    """
    interval = interval or active_interval(symbol)
    if interval is None:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bars WHERE symbol=? AND interval=? ORDER BY ts DESC LIMIT ?",
            (symbol, interval, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def insert_analysis(symbol, ts, stage1, stage2, model, latency_ms, interval="1m",
                    price_at=None, atr_at=None):
    """`price_at` / `atr_at` capture the market at the moment of analysis, so
    staleness can be judged later without guessing."""
    with conn() as c:
        c.execute(
            "INSERT INTO analyses (symbol, ts, stage1, stage2, model, latency_ms, "
            "interval, price_at, atr_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (symbol, ts, json.dumps(stage1), json.dumps(stage2), model, latency_ms,
             interval, price_at, atr_at),
        )


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


# --- paper trading ---

_TRADE_COLS = ("symbol", "direction", "order_type", "status", "entry", "stop",
               "target", "created_ts", "filled_ts", "closed_ts", "exit_price",
               "exit_reason", "r_multiple", "bars_pending")


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
