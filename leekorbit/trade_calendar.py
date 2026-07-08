"""A股交易日历：akshare 拉取 + 本地缓存，断供时退化为「工作日即交易日」。"""
from __future__ import annotations

import datetime as dt
import logging

from . import db

log = logging.getLogger(__name__)

_SESSIONS = ((dt.time(9, 30), dt.time(11, 30)), (dt.time(13, 0), dt.time(15, 0)))


def _fetch_remote() -> list[str]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    return [d.strftime("%Y-%m-%d") for d in df["trade_date"]]


def trade_dates() -> set[str] | None:
    """返回已知交易日集合；完全拿不到时返回 None（调用方退化处理）。"""
    cached = db.kv_get("trade_dates")
    today = dt.date.today().isoformat()
    if cached and cached["fetched"] == today:
        return set(cached["dates"])
    try:
        dates = _fetch_remote()
        db.kv_set("trade_dates", {"fetched": today, "dates": dates})
        return set(dates)
    except Exception as e:  # 网络断供：用旧缓存，再不行退化
        log.warning("trade calendar fetch failed: %s", e)
        if cached:
            return set(cached["dates"])
        return None


def is_trading_day(d: dt.date | None = None) -> bool:
    d = d or dt.date.today()
    dates = trade_dates()
    if dates is None:
        return d.weekday() < 5  # 降级：工作日即交易日
    return d.isoformat() in dates


def in_session(t: dt.datetime | None = None) -> bool:
    """当前是否处于连续竞价时段（不含集合竞价）。"""
    t = t or dt.datetime.now()
    if not is_trading_day(t.date()):
        return False
    now = t.time()
    return any(start <= now <= end for start, end in _SESSIONS)
