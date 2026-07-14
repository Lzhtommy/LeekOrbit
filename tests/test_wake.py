import datetime as dt
import json

import pytest

from leekorbit import config, datafeed, db, exchange, memory
from leekorbit.tools import ActionSpace
from leekorbit.wake import run_wake

NOW = dt.datetime(2026, 7, 8, 10, 35)
PERSONA = {
    "name": "leek-01", "display_name": "老李",
    "background": "32岁，采购专员，攒了10万入市，没有投资经验。",
    "risk_profile": "能承受一定波动", "capital": 100000,
    "permissions": ["main_board"],
}

QUOTES = {
    "000977": {"symbol": "000977", "last": 78.17, "prev_close": 71.06, "limit_up": 78.17,
               "limit_down": 63.95, "open": 72.0, "high": 78.17, "low": 71.5, "pct": 10.01, "turnover": 5.0},
    "601398": {"symbol": "601398", "last": 7.36, "prev_close": 7.26, "limit_up": 7.99,
               "limit_down": 6.53, "open": 7.22, "high": 7.37, "low": 7.16, "pct": 1.38, "turnover": 0.07},
}


@pytest.fixture(autouse=True)
def offline_world(monkeypatch):
    monkeypatch.setattr(datafeed, "indices", lambda: [{"name": "上证指数", "last": 3986.4, "pct": -0.1}])
    monkeypatch.setattr(datafeed, "market_breadth", lambda: None)
    monkeypatch.setattr(datafeed, "hot_rank",
                        lambda top=10: [{"rank": 1, "symbol": "000977", "name": "浪潮信息",
                                         "last": 78.17, "pct": 10.01}])
    monkeypatch.setattr(datafeed, "hot_up", lambda top=5: None)
    monkeypatch.setattr(datafeed, "zt_pool", lambda: None)
    monkeypatch.setattr(datafeed, "cls_news", lambda limit=8: None)
    monkeypatch.setattr(datafeed, "guba_posts", lambda s, limit=5: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: QUOTES.get(s))
    monkeypatch.setattr(datafeed, "stock_name",
                        lambda s: {"000977": "浪潮信息", "601398": "工商银行"}.get(s, s))
    monkeypatch.setattr(datafeed, "stock_news", lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "stock_comment", lambda s: None)
    monkeypatch.setattr(datafeed, "daily_kline", lambda *a, **k: None)
    monkeypatch.setattr(config, "load_platform", lambda: {"agent": {"lookup_limit": 5}})
    monkeypatch.delenv("LEEK_LLM_API_KEY", raising=False)


def new_wake(scene):
    return db.insert("wakes", ts=NOW.isoformat(), agent="leek-01", scene=scene,
                     status="running", detail=None)


# 注意：limit_up=78.17 会封板拒单——盘中场景改用现价<涨停的行情
QUOTES["000977"]["limit_up"] = 78.20


def test_intraday_wake_escalates_and_trades():
    wake_id = new_wake("intraday")
    summary = run_wake(PERSONA, "intraday", NOW, wake_id)
    assert "1 trades" in summary and "[mock]" in summary

    snap = db.one("SELECT * FROM snapshots WHERE wake_id=?", wake_id)
    assert snap is not None
    assert "浪潮信息" in snap["feed"]                       # 完整输入
    phases = json.loads(snap["transcript"])
    assert [p["tier"] for p in phases] == ["cheap", "strong"]  # 便宜层想下单 → 升级
    actions = json.loads(snap["actions"])
    receipts = [a for a in actions if str(a["result"]).startswith("成交回执")]
    assert len(receipts) == 1                               # 只有强层的单被执行
    pos = exchange.positions("leek-01")
    assert pos["000977"]["qty"] == 100                      # 账本真实成交


def test_rejection_fed_back_to_agent():
    QUOTES["000977"]["last"] = QUOTES["000977"]["limit_up"]  # 封板
    try:
        wake_id = new_wake("intraday")
        run_wake(PERSONA, "intraday", NOW, wake_id)
        actions = json.loads(db.one("SELECT actions FROM snapshots WHERE wake_id=?", wake_id)["actions"])
        rejected = [a for a in actions if "委托被拒" in str(a["result"])]
        assert rejected and "涨停" in rejected[0]["result"]   # 拒因原样反馈给 Agent
        assert exchange.positions("leek-01") == {}
    finally:
        QUOTES["000977"]["last"] = 78.17


def test_evening_wake_writes_diary():
    wake_id = new_wake("evening")
    run_wake(PERSONA, "evening", NOW.replace(hour=20, minute=30), wake_id)
    entries = memory.recent("leek-01")
    assert entries and "看了会盘" in entries[-1]["content"]
    phases = json.loads(db.one("SELECT transcript FROM snapshots WHERE wake_id=?", wake_id)["transcript"])
    assert [p["tier"] for p in phases] == ["strong"]        # 晚间直接走强层


def test_ledger_history_never_enters_llm_context():
    # 昨天买入并清仓的 600519：客观账本有记录，但不应出现在任何喂给 LLM 的内容里
    d1, d2 = NOW - dt.timedelta(days=2), NOW - dt.timedelta(days=1)
    exchange.deposit("leek-01", 100000, d1 - dt.timedelta(days=1))
    exchange.place_order("leek-01", "buy", 100, exchange.Quote("600519", "贵州茅台", 700.0, 700.0), now=d1)
    exchange.place_order("leek-01", "sell", 100,
                         exchange.Quote("600519", "贵州茅台", 690.0, 700.0), now=d2)
    wake_id = new_wake("intraday")
    run_wake(PERSONA, "intraday", NOW, wake_id)
    snap = db.one("SELECT feed, transcript FROM snapshots WHERE wake_id=?", wake_id)
    assert "600519" not in snap["feed"]
    for phase in json.loads(snap["transcript"]):
        for m in phase["messages"]:
            assert "600519" not in str(m.get("content") or "")


def test_lookup_limit_enforced():
    space = ActionSpace(PERSONA, "intraday", NOW, lookup_limit=1)
    first = space.dispatch("lookup_stock", {"symbol": "601398"})
    assert "工商银行" in first
    second = space.dispatch("lookup_stock", {"symbol": "000977"})
    assert "已用完" in second
