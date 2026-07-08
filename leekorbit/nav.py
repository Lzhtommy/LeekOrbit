"""每日净值结算：现金 + 按收盘价的持仓市值。只供研究者，不进 Agent 信息流。"""
from __future__ import annotations

import datetime as dt
import logging

from . import datafeed, db, exchange, trade_calendar

log = logging.getLogger(__name__)


def _close_price(symbol: str) -> float | None:
    q = datafeed.quote(symbol)
    if q:
        return q["last"]
    kline = datafeed.daily_kline(symbol, 3)
    if kline:
        return kline[-1]["close"]
    return None


def _benchmark() -> float | None:
    idx = datafeed.indices()
    if idx:
        for i in idx:
            if i["name"] == "沪深300":
                return i["last"]
    return None


def settle(agent: str, date: dt.date | None = None) -> dict | None:
    """结算某日净值并落库（幂等）。数据不全时放弃本次结算，绝不写入错误数字。"""
    date = date or dt.date.today()
    if not trade_calendar.is_trading_day(date):
        return None
    cash = exchange.cash(agent)
    market_value = 0.0
    for sym, p in exchange.positions(agent).items():
        price = _close_price(sym)
        if price is None:
            log.warning("nav settle aborted: no price for %s", sym)
            return None
        market_value += p["qty"] * price
    row = {
        "date": date.isoformat(), "agent": agent, "cash": round(cash, 2),
        "market_value": round(market_value, 2), "nav": round(cash + market_value, 2),
        "benchmark": _benchmark(),
    }
    db.conn().execute(
        "INSERT INTO nav (date, agent, cash, market_value, nav, benchmark) "
        "VALUES (:date, :agent, :cash, :market_value, :nav, :benchmark) "
        "ON CONFLICT(date, agent) DO UPDATE SET cash=:cash, market_value=:market_value, "
        "nav=:nav, benchmark=:benchmark",
        row,
    )
    db.conn().commit()
    return row


def series(agent: str) -> list[dict]:
    return [dict(r) for r in db.rows(
        "SELECT date, cash, market_value, nav, benchmark FROM nav WHERE agent=? ORDER BY date", agent
    )]
