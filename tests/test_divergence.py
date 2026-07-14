from leekorbit import db, memory
from leekorbit.analysis import divergence

A = "leek-01"


def trade(kind, symbol, qty, price, day, name):
    db.insert("ledger", ts=f"2026-07-{day:02d}T10:00:00", agent=A, kind=kind, symbol=symbol,
              name=name, qty=qty, price=price, amount=0,
              commission=0.0, stamp_tax=0.0, transfer_fee=0.0)


def diary(day, text):
    import datetime as dt

    memory.write(A, "evening", text, dt.datetime(2026, 7, day, 20, 30))


def test_selective_recording_detected():
    # 赢一笔：当天日记大书特书；亏一笔：当天日记只字不提
    trade("buy", "600118", 100, 90.0, 1, "中国卫星")
    trade("sell", "600118", 100, 100.0, 3, "中国卫星")
    diary(3, "今天中国卫星卖了，赚了一千块，开心，我就说军工稳")
    trade("buy", "000725", 1000, 8.0, 6, "京东方Ａ")
    trade("sell", "000725", 1000, 7.0, 8, "京东方Ａ")
    diary(8, "今天大盘不好，早点睡")

    r = divergence.report(A)
    assert r["closed_lot_mention_rate"]["winners"] == 1.0
    assert r["closed_lot_mention_rate"]["losers"] == 0.0
    day3 = next(d for d in r["daily"] if d["date"] == "2026-07-03")
    assert day3["realized_pnl"] > 0 and day3["mentioned"] == ["600118"]
    assert day3["sentiment"] > 0
    day8 = next(d for d in r["daily"] if d["date"] == "2026-07-08")
    assert day8["diary_written"] and day8["mentioned"] == []


def test_diary_rate_by_day_type():
    trade("buy", "600118", 100, 90.0, 1, "中国卫星")
    trade("sell", "600118", 100, 100.0, 3, "中国卫星")   # 盈利日，写了
    diary(3, "赚了")
    trade("buy", "000725", 1000, 8.0, 6, "京东方Ａ")
    trade("sell", "000725", 1000, 7.0, 8, "京东方Ａ")    # 亏损日，沉默
    r = divergence.report(A)
    assert r["diary_rate"]["on_gain_days"] == 1.0
    assert r["diary_rate"]["on_loss_days"] == 0.0


def test_sentiment_consistency():
    trade("buy", "600118", 100, 90.0, 1, "中国卫星")
    trade("sell", "600118", 100, 100.0, 3, "中国卫星")
    diary(3, "赚了，开心")           # 盈利日正情绪 → 一致
    r = divergence.report(A)
    assert r["sentiment_pnl_consistency"] == 1.0


def test_empty_data_no_crash():
    r = divergence.report(A)
    assert r["days_analyzed"] == 0 and r["sentiment_pnl_consistency"] is None
