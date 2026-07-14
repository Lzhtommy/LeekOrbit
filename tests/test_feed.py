import datetime as dt

import pytest

from leekorbit import datafeed, exchange, feed, memory, routine

A = "leek-01"
NOW = dt.datetime(2026, 7, 8, 10, 30)

CANNED = {
    "indices": [{"name": "上证指数", "last": 3986.4, "pct": -0.1}],
    "breadth": {"up": 1451, "down": 3596, "limit_up": 19, "limit_down": 16},
    "hot": [{"rank": 1, "symbol": "000977", "name": "浪潮信息", "last": 78.17, "pct": 10.01}],
    "quote": {"symbol": "601398", "last": 7.36, "prev_close": 7.26, "limit_up": 7.99,
              "limit_down": 6.53, "open": 7.22, "high": 7.37, "low": 7.16, "pct": 1.38, "turnover": 0.07},
}


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(datafeed, "indices", lambda: CANNED["indices"])
    monkeypatch.setattr(datafeed, "market_breadth", lambda: CANNED["breadth"])
    monkeypatch.setattr(datafeed, "hot_rank", lambda top=10: CANNED["hot"])
    monkeypatch.setattr(datafeed, "hot_up", lambda top=5: None)
    monkeypatch.setattr(datafeed, "zt_pool", lambda: None)
    monkeypatch.setattr(datafeed, "cls_news", lambda limit=8: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: CANNED["quote"])
    monkeypatch.setattr(datafeed, "stock_news", lambda s, limit=5: [{"time": "t", "title": "工行发布公告"}])


@pytest.fixture
def offline(monkeypatch):
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up", "zt_pool", "cls_news"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    monkeypatch.setattr(datafeed, "stock_news", lambda *a, **k: None)


def test_intraday_feed_contains_market_hot_and_account(online):
    exchange.deposit(A, 100000, NOW - dt.timedelta(days=2))
    text = feed.build(A, routine.INTRADAY, NOW)
    assert "上证指数" in text and "上涨1451家" in text
    assert "浪潮信息" in text
    assert "可用资金 100,000.00" in text


def test_positions_pnl_rendered(online):
    exchange.deposit(A, 100000, NOW - dt.timedelta(days=2))
    exchange.place_order(A, "buy", 1000, exchange.Quote("601398", "工商银行", 7.0, 7.0),
                         now=NOW - dt.timedelta(days=1))
    text = feed.build(A, routine.INTRADAY, NOW)
    assert "601398" in text and "浮动盈亏" in text


def test_offline_degrades_honestly(offline):
    exchange.deposit(A, 100000, NOW - dt.timedelta(days=2))
    exchange.place_order(A, "buy", 1000, exchange.Quote("601398", "工商银行", 7.0, 7.0),
                         now=NOW - dt.timedelta(days=1))
    text = feed.build(A, routine.INTRADAY, NOW)
    assert "加载失败" in text          # 如实标注
    assert "7.36" not in text          # 不编造行情
    assert "601398" in text            # 持仓本身仍可见（本地账本推导）


def test_evening_feed_has_fills_and_diary(online):
    exchange.deposit(A, 100000, NOW - dt.timedelta(days=2))
    exchange.place_order(A, "buy", 1000, exchange.Quote("601398", "工商银行", 7.0, 7.0), now=NOW)
    memory.write(A, "evening", "今天买了工行，感觉银行股稳", NOW - dt.timedelta(days=1))
    text = feed.build(A, routine.EVENING, NOW)
    assert "今日成交" in text and "买入 601398" in text
    assert "我之前的笔记" in text and "感觉银行股稳" in text
