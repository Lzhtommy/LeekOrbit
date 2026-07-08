"""SQLite 存储层。账本（Ledger）是唯一事实：现金、持仓全部由账本推导。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB_PATH = Path("data/leekorbit.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS wakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    scene TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',  -- done / skipped / failed
    detail TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wake_id INTEGER NOT NULL REFERENCES wakes(id),
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    scene TEXT NOT NULL,
    feed TEXT NOT NULL,            -- 推送给 Agent 的信息流全文
    transcript TEXT NOT NULL,      -- 模型往返（含查询结果、思考、动作）JSON
    actions TEXT NOT NULL,         -- 动作及结果列表 JSON
    cost TEXT                      -- token 用量与成本 JSON
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,            -- deposit / buy / sell
    symbol TEXT,
    name TEXT,
    qty INTEGER,
    price REAL,
    amount REAL NOT NULL,          -- 现金变动（买入为负，卖出/入金为正，已含费用）
    commission REAL DEFAULT 0,
    stamp_tax REAL DEFAULT 0,
    transfer_fee REAL DEFAULT 0,
    note TEXT
);
CREATE TABLE IF NOT EXISTS diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    scene TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nav (
    date TEXT NOT NULL,
    agent TEXT NOT NULL,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    nav REAL NOT NULL,
    benchmark REAL,                -- 当日沪深300收盘点位
    PRIMARY KEY (date, agent)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    type TEXT NOT NULL,            -- permission_unlocked / ...
    payload TEXT,
    notified INTEGER DEFAULT 0     -- 是否已通过信息流告知 Agent
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    wake_id INTEGER,
    tier TEXT NOT NULL,            -- cheap / strong
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_est REAL DEFAULT 0        -- 估算成本（元）
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_local = threading.local()
_db_path: Path = DEFAULT_DB_PATH


def configure(path: str | Path) -> None:
    global _db_path
    _db_path = Path(path)
    if hasattr(_local, "conn"):
        _local.conn.close()
        del _local.conn


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(_db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)
        _local.conn = c
    return c


def insert(table: str, **cols) -> int:
    keys = ", ".join(cols)
    ph = ", ".join("?" for _ in cols)
    cur = conn().execute(f"INSERT INTO {table} ({keys}) VALUES ({ph})", list(cols.values()))
    conn().commit()
    return cur.lastrowid


def rows(sql: str, *args) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def one(sql: str, *args) -> sqlite3.Row | None:
    return conn().execute(sql, args).fetchone()


def kv_get(key: str, default=None):
    r = one("SELECT value FROM kv WHERE key=?", key)
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value) -> None:
    conn().execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn().commit()
