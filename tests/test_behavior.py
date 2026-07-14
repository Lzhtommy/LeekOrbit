import datetime as dt

from leekorbit import db
from leekorbit.analysis import behavior

A = "leek-01"


def trade(kind, symbol, qty, price, day, name="某股"):
    db.insert("ledger", ts=f"2026-07-{day:02d}T10:00:00", agent=A, kind=kind, symbol=symbol,
              name=name, qty=qty, price=price, amount=0,
              commission=5.0, stamp_tax=0.0, transfer_fee=0.1)


def tags_of(result):
    return [e["tag"] for e in result["events"]]


def test_chase_up_needs_day_pct():
    trade("buy", "600118", 100, 90.0, 1)
    r = behavior.tag_trades(A, day_pct=lambda s, d: 7.2)
    assert tags_of(r) == ["chase_up"]
    assert "+7.2%" in r["events"][0]["evidence"]
    assert behavior.tag_trades(A)["events"] == []  # 无数据源时静默跳过，不猜


def test_average_down_and_pyramid_up():
    trade("buy", "600118", 100, 90.0, 1)
    trade("buy", "600118", 100, 80.0, 2)    # 低于成本 3%+ → 补仓
    trade("buy", "600118", 100, 95.0, 3)    # 高于上次买价 3%+ → 越涨越买
    r = behavior.tag_trades(A)
    assert tags_of(r) == ["average_down", "pyramid_up"]


def test_cut_loss_and_fast_profit():
    trade("buy", "600000", 1000, 10.0, 1)
    trade("sell", "600000", 1000, 9.0, 5)    # 亏损离场
    trade("buy", "000001", 1000, 20.0, 6)
    trade("sell", "000001", 1000, 22.0, 7)   # 盈利但只持有1天
    r = behavior.tag_trades(A)
    assert tags_of(r) == ["cut_loss", "take_profit_fast"]
    assert r["summary"] == {"cut_loss": 1, "take_profit_fast": 1}


def test_hold_loser_detected_with_price_provider():
    trade("buy", "600118", 100, 90.0, 1)
    r = behavior.tag_trades(A, current_price=lambda s: 80.0, asof=dt.date(2026, 7, 20))
    assert tags_of(r) == ["hold_loser"]
    assert "19天" in r["events"][0]["evidence"]
    # 浮亏不足或时间不够都不算
    assert behavior.tag_trades(A, current_price=lambda s: 88.0, asof=dt.date(2026, 7, 20))["events"] == []
    assert behavior.tag_trades(A, current_price=lambda s: 80.0, asof=dt.date(2026, 7, 5))["events"] == []


def test_readonly():
    trade("buy", "600000", 100, 10.0, 1)
    before = db.one("SELECT COUNT(*) n FROM ledger")["n"]
    behavior.tag_trades(A, day_pct=lambda s, d: 9.9, current_price=lambda s: 5.0)
    assert db.one("SELECT COUNT(*) n FROM ledger")["n"] == before
