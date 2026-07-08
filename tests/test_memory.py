import datetime as dt

from leekorbit import datafeed, exchange, feed, memory, routine

A = "leek-01"
D1 = dt.datetime(2026, 7, 8, 20, 30)
D2 = dt.datetime(2026, 7, 9, 9, 10)


def test_diary_verbatim_recall_next_day(monkeypatch):
    """晚间写的日记，次日盘前信息流原文回带——平台不加工、不补充。"""
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    monkeypatch.setattr(datafeed, "stock_news", lambda *a, **k: None)

    exchange.deposit(A, 100000, D1 - dt.timedelta(days=1))
    raw = "浪潮信息今天没买成，涨停了排不上，明天低开我就冲"
    memory.write(A, routine.EVENING, raw, D1)

    text = feed.build(A, routine.PREMARKET, D2)
    assert raw in text                      # 原文一字不差
    assert "我之前的笔记" in text

    entries = memory.recent(A, 5)
    assert entries[-1]["content"] == raw    # 存取无加工


def test_recall_limit_keeps_only_recent():
    for i in range(10):
        memory.write(A, routine.EVENING, f"第{i}天的想法", dt.datetime(2026, 7, i + 1, 20, 30))
    entries = memory.recent(A, 3)
    assert [e["content"] for e in entries] == ["第7天的想法", "第8天的想法", "第9天的想法"]
