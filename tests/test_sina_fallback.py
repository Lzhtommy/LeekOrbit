import pytest

from leekorbit import datafeed


def sina_fields(name="京东方Ａ", open_=7.69, prev=7.58, last=7.67, high=7.90, low=7.26):
    f = [name, str(open_), str(prev), str(last), str(high), str(low)]
    f += ["0"] * 24 + ["2026-07-08", "14:30:00", "00"]
    return f


def test_sina_quote_parsing(monkeypatch):
    monkeypatch.setattr(datafeed, "_sina_hq", lambda codes: [sina_fields()])
    q = datafeed._fetch_quote_sina("000725")
    assert q["last"] == 7.67 and q["prev_close"] == 7.58
    assert q["limit_up"] == round(7.58 * 1.1, 2)   # 主板 10% 由昨收推算
    assert q["limit_down"] == round(7.58 * 0.9, 2)
    assert q["pct"] == round((7.67 / 7.58 - 1) * 100, 2)
    assert datafeed._sina_names["000725"] == "京东方Ａ"


def test_sina_st_gets_5_percent_band(monkeypatch):
    monkeypatch.setattr(datafeed, "_sina_hq", lambda codes: [sina_fields(name="ST某某", prev=10.0, last=10.2)])
    q = datafeed._fetch_quote_sina("600123")
    assert q["limit_up"] == 10.5 and q["limit_down"] == 9.5


def test_sina_suspended_stock_raises(monkeypatch):
    monkeypatch.setattr(datafeed, "_sina_hq", lambda codes: [sina_fields(last=0)])
    with pytest.raises(ValueError):
        datafeed._fetch_quote_sina("000725")


def test_sina_indices_parsing(monkeypatch):
    monkeypatch.setattr(datafeed, "_sina_hq", lambda codes: [
        ["上证指数", "3986.399", "-3.881", "-0.10", "3612992", "45028188"],
        ["沪深300", "4787.08", "-5.2", "-0.11", "100", "200"],
    ])
    idx = datafeed._fetch_indices_sina()
    assert idx[0] == {"name": "上证指数", "last": 3986.399, "pct": -0.10}


def test_prefix_mapping():
    assert datafeed._sina_prefix("600000") == "sh"
    assert datafeed._sina_prefix("000725") == "sz"
    assert datafeed._sina_prefix("300750") == "sz"
    assert datafeed._sina_prefix("830799") == "bj"


def test_display_name_heals_placeholder(monkeypatch):
    monkeypatch.setattr(datafeed, "stock_name", lambda s: "京东方Ａ")
    assert datafeed.display_name("000725", "000725") == "京东方Ａ"   # 占位名补查
    assert datafeed.display_name("000725", None) == "京东方Ａ"       # 空名补查
    assert datafeed.display_name("600118", "中国卫星") == "中国卫星"  # 正常名直接用
    monkeypatch.setattr(datafeed, "stock_name", lambda s: None)
    assert datafeed.display_name("000725", "000725") == "000725"     # 全失败退回代码
