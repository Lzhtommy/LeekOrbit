import pytest

from leekorbit import datafeed, feed, routine


def tencent_vals(name="京东方Ａ", last="7.67", prev="7.58"):
    v = ["0"] * 60
    v[1], v[2], v[3], v[4], v[5] = name, "000725", last, prev, "7.69"
    v[31], v[32], v[33], v[34] = "0.09", "1.19", "7.90", "7.26"
    v[37], v[38] = "187040", "4.55"
    v[47], v[48] = "8.34", "6.82"
    return v


def test_tencent_quote_parsing():
    q = datafeed._parse_tencent_quote("000725", tencent_vals())
    assert q["last"] == 7.67 and q["prev_close"] == 7.58
    assert q["limit_up"] == 8.34 and q["limit_down"] == 6.82  # 腾讯给权威涨跌停价
    assert q["turnover"] == 4.55
    assert datafeed._sina_names["000725"] == "京东方Ａ"


def test_tencent_suspended_raises():
    with pytest.raises(ValueError):
        datafeed._parse_tencent_quote("000725", tencent_vals(last="0"))


def test_tencent_prefix():
    assert datafeed._tencent_prefix("600000") == "sh"
    assert datafeed._tencent_prefix("000725") == "sz"
    assert datafeed._tencent_prefix("830799") == "bj"


def test_zt_pool_parsing(monkeypatch):
    monkeypatch.setattr(datafeed, "_zt_raw", lambda date: [
        {"c": 600118, "n": "中国卫星", "hybk": "航天航空", "lbc": 3, "zbc": 1,
         "zttj": {"days": 5, "ct": 3}},
        {"c": 2185, "n": "华天科技", "hybk": "半导体", "lbc": 1, "zbc": 0,
         "zttj": {"days": 1, "ct": 1}},
    ])
    datafeed._cache.pop("zt_pool", None)
    pool = datafeed.zt_pool()
    assert pool[0]["name"] == "中国卫星" and pool[0]["zt_stat"] == "5天3板"
    assert pool[1]["symbol"] == "002185"  # 代码补零


def test_feed_zt_block(monkeypatch):
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up", "cls_news"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    monkeypatch.setattr(datafeed, "zt_pool", lambda: [
        {"symbol": "600118", "name": "中国卫星", "industry": "航天航空",
         "limit_days": 3, "break_times": 1, "zt_stat": "5天3板"}])
    text = feed.build("leek-01", routine.INTRADAY)
    assert "【涨停风向】今日 1 只涨停，最高 3 连板" in text
    assert "中国卫星(600118) 5天3板 [航天航空] 炸板1次" in text


def test_feed_flash_block(monkeypatch):
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up", "zt_pool", "stock_news"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    monkeypatch.setattr(datafeed, "cls_news", lambda limit=8: [
        {"time": "08:30", "title": "央行开展1000亿元逆回购操作"}])
    text = feed.build("leek-01", routine.PREMARKET)
    assert "【财经快讯】" in text and "央行开展1000亿元逆回购操作" in text


def test_blocks_omitted_silently_when_unavailable(monkeypatch):
    for fn in ("indices", "market_breadth", "hot_rank", "hot_up", "zt_pool", "cls_news", "stock_news"):
        monkeypatch.setattr(datafeed, fn, lambda *a, **k: None)
    monkeypatch.setattr(datafeed, "quote", lambda s: None)
    text = feed.build("leek-01", routine.INTRADAY)
    assert "涨停风向" not in text and "财经快讯" not in text  # 调味料缺失不报错不占位
