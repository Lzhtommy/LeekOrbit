import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from leekorbit import datafeed, db, exchange
from leekorbit.dashboard.app import app

A = "leek-01"


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(datafeed, "quote", lambda s: {"last": 8.0, "pct": 1.0})
    exchange.deposit(A, 100000, dt.datetime(2026, 7, 6))
    exchange.place_order(A, "buy", 1000, exchange.Quote("601398", "工商银行", 7.0, 7.0),
                         now=dt.datetime(2026, 7, 7, 10, 0))


client = TestClient(app)


def test_summary_matches_ledger():
    s = client.get("/api/summary").json()
    assert s["cash"] == exchange.cash(A)
    assert s["positions"][0]["symbol"] == "601398"
    assert s["positions"][0]["pnl"] > 0
    assert s["total"] == s["cash"] + s["market_value"]


def test_timeline_and_snapshot_replay():
    wake_id = db.insert("wakes", ts="2026-07-08T10:35:00", agent=A, scene="intraday",
                        status="done", detail="2 actions, 1 trades")
    db.insert("snapshots", wake_id=wake_id, ts="2026-07-08T10:35:00", agent=A, scene="intraday",
              feed="【大盘】...", transcript=json.dumps([{"tier": "cheap", "messages": []}]),
              actions=json.dumps([{"tool": "place_order", "args": {}, "result": "成交回执：..."}]),
              cost=json.dumps({"cost_cny": 0.01}))
    wakes = client.get("/api/wakes").json()
    assert wakes[0]["snapshot_id"] is not None
    snap = client.get(f"/api/snapshot/{wakes[0]['snapshot_id']}").json()
    assert snap["feed"].startswith("【大盘】")
    assert snap["actions"][0]["result"].startswith("成交回执")


def test_index_served():
    r = client.get("/")
    assert "韭菜观察站" in r.text
