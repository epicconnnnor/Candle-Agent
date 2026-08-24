"""SQLite storage for bars and analyses."""
import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "candle_agent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    stage1 TEXT NOT NULL,
    stage2 TEXT NOT NULL,
    model TEXT,
    latency_ms INTEGER
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


@contextmanager
def conn():
    # WAL + busy_timeout: several service processes (ingest, analyzer, api)
    # share this file concurrently. WAL allows readers during a write;
    # busy_timeout makes writers wait instead of failing on a locked db.
    c = sqlite3.connect(DB_PATH, timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def insert_bar(symbol, ts, o, h, l, cl, v):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
            (symbol, ts, o, h, l, cl, v),
        )


def recent_bars(symbol, limit=100):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bars WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def insert_analysis(symbol, ts, stage1, stage2, model, latency_ms):
    with conn() as c:
        c.execute(
            "INSERT INTO analyses (symbol, ts, stage1, stage2, model, latency_ms) VALUES (?,?,?,?,?,?)",
            (symbol, ts, json.dumps(stage1), json.dumps(stage2), model, latency_ms),
        )


def latest_analysis(symbol):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM analyses WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,)
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


def trade_history(symbol: str, limit: int = 50):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM paper_trades WHERE symbol=? AND status NOT IN ('pending','open') "
            "ORDER BY id DESC LIMIT ?", (symbol, limit)).fetchall()
    return [dict(r) for r in rows]
