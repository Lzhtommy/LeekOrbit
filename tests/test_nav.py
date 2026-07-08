import datetime as dt

import pytest

from leekorbit import datafeed, db, exchange, nav, trade_calendar

A = "leek-01"
D = dt.date(2026, 7, 8)


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(trade_calendar, "is_trading_day", lambda d=None: d.weekday() < 5)
    monkeypatch.setattr(datafeed, "quote", lambda s: {"last": 8.0, "pct": 1.0} if s == "601398" else None)
    monkeypatch.setattr(datafeed, "daily_kline", lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "indices",
                        lambda: [{"name": "沪深300", "last": 4787.08, "pct": -0.11}])
    exchange.deposit(A, 100000, dt.datetime(2026, 7, 6, 9, 0))
    exchange.place_order(A, "buy", 1000, exchange.Quote("601398", "工商银行", 7.0, 7.0),
                         now=dt.datetime(2026, 7, 7, 10, 0))


def test_settle_writes_nav_consistent_with_ledger():
    row = nav.settle(A, D)
    assert row["market_value"] == 8000.0
    assert row["nav"] == row["cash"] + 8000.0
    assert row["cash"] == exchange.cash(A)
    assert row["benchmark"] == 4787.08


def test_settle_idempotent_upsert():
    nav.settle(A, D)
    nav.settle(A, D)
    assert len(nav.series(A)) == 1


def test_non_trading_day_skipped():
    assert nav.settle(A, dt.date(2026, 7, 11)) is None
    assert nav.series(A) == []


def test_missing_price_aborts_not_fabricates(monkeypatch):
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    assert nav.settle(A, D) is None
    assert nav.series(A) == []
