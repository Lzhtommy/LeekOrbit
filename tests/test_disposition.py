import datetime as dt

from leekorbit import db, exchange
from leekorbit.analysis import disposition

A = "leek-01"


def trade(kind, symbol, qty, price, day, fees=(5.0, 0.0, 0.1)):
    db.insert("ledger", ts=f"2026-07-{day:02d}T10:00:00", agent=A, kind=kind, symbol=symbol,
              name=symbol, qty=qty, price=price, amount=0,
              commission=fees[0], stamp_tax=fees[1], transfer_fee=fees[2])


def test_empty_ledger_no_crash():
    r = disposition.report(A)
    assert r["closed_lots"] == 0
    assert r["disposition_signal"] is None


def test_open_position_only():
    trade("buy", "600000", 1000, 10.0, 1)
    r = disposition.report(A, asof=dt.date(2026, 7, 11))
    assert r["closed_lots"] == 0
    assert r["holding_days_distribution"] == {"6-20天": 1}


def test_fifo_matching_and_disposition_signal():
    # 盈利单：持有2天就跑；亏损单：扛了10天才割——典型处置效应
    trade("buy", "600000", 1000, 10.0, 1)
    trade("sell", "600000", 1000, 11.0, 3)      # +1元/股，持有2天
    trade("buy", "000001", 1000, 20.0, 2)
    trade("sell", "000001", 1000, 18.0, 12)     # -2元/股，持有10天
    r = disposition.report(A, asof=dt.date(2026, 7, 15))
    assert r["closed_lots"] == 2
    assert r["winners"]["count"] == 1 and r["winners"]["avg_holding_days"] == 2
    assert r["losers"]["count"] == 1 and r["losers"]["avg_holding_days"] == 10
    assert r["disposition_signal"] == 5.0       # 扛亏时长是拿盈的5倍
    assert r["holding_days_distribution"] == {"2-5天": 1, "6-20天": 1}


def test_partial_sell_fifo_split():
    trade("buy", "600000", 1000, 10.0, 1)
    trade("buy", "600000", 1000, 12.0, 2)
    trade("sell", "600000", 1500, 13.0, 5)      # 吃掉第一批全部+第二批一半
    lots = disposition.closed_lots(A)
    assert [(l["qty"], l["buy_date"]) for l in lots] == \
        [(1000, "2026-07-01"), (500, "2026-07-02")]
    r = disposition.report(A, asof=dt.date(2026, 7, 6))
    assert r["holding_days_distribution"]["2-5天"] == 3  # 两个平仓批次 + 一个在持批次
    assert sum(disposition.open_holding_days(A, dt.date(2026, 7, 6))) == 4  # 剩500股持有4天


def test_readonly_no_writes():
    trade("buy", "600000", 100, 10.0, 1)
    before = db.one("SELECT COUNT(*) n FROM ledger")["n"]
    disposition.report(A)
    assert db.one("SELECT COUNT(*) n FROM ledger")["n"] == before
    assert db.one("SELECT COUNT(*) n FROM diary")["n"] == 0
