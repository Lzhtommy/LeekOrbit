import datetime as dt

import pytest

from leekorbit import datafeed, db, exchange, feed, permissions, routine
from leekorbit.tools import ActionSpace, current_permissions

A = "leek-01"
PERSONA = {"name": A, "background": "x", "capital": 100000, "permissions": ["main_board"],
           "display_name": "老李", "risk_profile": ""}
NOW = dt.datetime(2026, 8, 5, 10, 0)


def seed_nav(days: int, nav_value: float):
    for i in range(days):
        d = dt.date(2026, 7, 1) + dt.timedelta(days=i)
        db.insert("nav", date=d.isoformat(), agent=A, cash=nav_value,
                  market_value=0, nav=nav_value, benchmark=None)


def test_not_enough_days_no_unlock():
    seed_nav(10, 120000)
    assert permissions.check_chinext_unlock(A, PERSONA) is False


def test_low_assets_no_unlock():
    seed_nav(25, 60000)
    assert permissions.check_chinext_unlock(A, PERSONA) is False


def test_unlock_once_and_notify_via_feed(monkeypatch):
    seed_nav(25, 110000)
    assert permissions.check_chinext_unlock(A, PERSONA) is True
    assert permissions.check_chinext_unlock(A, PERSONA) is False  # 幂等
    assert "chinext" in current_permissions(A, PERSONA)

    # 解锁通知以券商消息出现在下一次唤醒的信息流中，且只出现一次
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    monkeypatch.setattr(datafeed, "stock_news", lambda *a, **k: None)
    text = feed.build(A, routine.INTRADAY, NOW)
    assert "创业板交易权限已为您开通" in text
    assert "创业板" not in feed.build(A, routine.INTRADAY, NOW)


def test_chinext_order_flow_before_and_after(monkeypatch):
    monkeypatch.setattr(datafeed, "quote", lambda s: {
        "symbol": s, "last": 50.0, "prev_close": 45.0, "limit_up": 54.0, "limit_down": 36.0,
        "open": 46.0, "high": 51.0, "low": 45.5, "pct": 11.1, "turnover": 3.0})
    monkeypatch.setattr(datafeed, "stock_name", lambda s: "宁德时代")
    exchange.deposit(A, 100000, NOW - dt.timedelta(days=2))

    space = ActionSpace(PERSONA, "intraday", NOW)
    r = space.dispatch("place_order", {"side": "buy", "symbol": "300750", "qty": 100})
    assert "未开通创业板" in r                     # 解锁前拒单且拒因明确

    seed_nav(25, 110000)
    permissions.check_chinext_unlock(A, PERSONA)
    space2 = ActionSpace(PERSONA, "intraday", NOW)
    r2 = space2.dispatch("place_order", {"side": "buy", "symbol": "300750", "qty": 100})
    assert r2.startswith("成交回执")               # 解锁后按 20% 档正常撮合（+11.1% 未封板）
