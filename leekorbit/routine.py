"""作息表（Routine）：由人设配置推导某一天的全部预期唤醒时点与场景。"""
from __future__ import annotations

import datetime as dt

from . import trade_calendar

# 场景常量
PREMARKET = "premarket"        # 盘前浏览
INTRADAY = "intraday"          # 盘中看盘
LUNCH = "lunch"                # 午休刷社媒
CLOSE_REVIEW = "close_review"  # 收盘复盘
EVENING = "evening"            # 晚间复盘（写日记）
WEEKEND = "weekend"            # 周末/休市日低频

DEFAULT_ROUTINE = {
    "premarket": "09:10",
    "intraday_interval_minutes": 30,
    "lunch": "12:00",
    "close_review": "15:10",
    "evening": "20:30",
    "weekend": "11:00",
}


def _t(date: dt.date, hhmm: str) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(date, dt.time(h, m))


def expected_wakes(date: dt.date, routine: dict | None = None) -> list[tuple[dt.datetime, str]]:
    """返回某天按作息表应发生的 (时点, 场景) 列表，升序。"""
    r = {**DEFAULT_ROUTINE, **(routine or {})}
    if not trade_calendar.is_trading_day(date):
        # 周末与节假日统一走低频场景，绝不安排盘中类唤醒
        return [(_t(date, r["weekend"]), WEEKEND)]

    wakes: list[tuple[dt.datetime, str]] = [(_t(date, r["premarket"]), PREMARKET)]
    step = dt.timedelta(minutes=int(r["intraday_interval_minutes"]))
    for start, end in (("09:35", "11:30"), ("13:05", "15:00")):
        t, session_end = _t(date, start), _t(date, end)
        while t <= session_end:
            wakes.append((t, INTRADAY))
            t += step
    wakes.append((_t(date, r["lunch"]), LUNCH))
    wakes.append((_t(date, r["close_review"]), CLOSE_REVIEW))
    wakes.append((_t(date, r["evening"]), EVENING))
    return sorted(wakes)
