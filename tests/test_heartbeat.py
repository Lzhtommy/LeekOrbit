import datetime as dt

import pytest

from leekorbit import db, routine, trade_calendar
from leekorbit.scheduler import Heartbeat

TRADING_DAY = dt.date(2026, 7, 8)   # 周三
HOLIDAY = dt.date(2026, 10, 1)      # 周四，国庆
SATURDAY = dt.date(2026, 7, 11)


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch):
    monkeypatch.setattr(
        trade_calendar, "is_trading_day", lambda d=None: d.weekday() < 5 and d != HOLIDAY
    )


def scenes_on(date):
    return [s for _, s in routine.expected_wakes(date)]


def test_trading_day_has_full_routine():
    scenes = scenes_on(TRADING_DAY)
    assert scenes[0] == routine.PREMARKET
    assert scenes.count(routine.INTRADAY) == 8  # 30分钟间隔：上午4次+下午4次
    for s in (routine.LUNCH, routine.CLOSE_REVIEW, routine.EVENING):
        assert s in scenes
    assert routine.WEEKEND not in scenes


def test_holiday_has_no_intraday_wakes():
    scenes = scenes_on(HOLIDAY)
    assert scenes == [routine.WEEKEND]


def test_weekend_low_frequency():
    assert scenes_on(SATURDAY) == [routine.WEEKEND]


def test_tick_fires_on_time_and_dedupes():
    hb = Heartbeat("leek-01", lookback_days=0)
    now = dt.datetime.combine(TRADING_DAY, dt.time(9, 36))
    fired = hb.tick(now)
    assert (routine.PREMARKET, "skipped") in fired  # 09:10 已过宽限期
    assert (routine.INTRADAY, "done") in fired      # 09:35 准点
    assert hb.tick(now) == []  # 同一时点再 tick 不重复


def test_missed_wakes_marked_skipped_not_replayed():
    hb = Heartbeat("leek-01", lookback_days=0)
    now = dt.datetime.combine(TRADING_DAY, dt.time(10, 36))
    fired = hb.tick(now)
    skipped = [s for s, st in fired if st == "skipped"]
    done = [s for s, st in fired if st == "done"]
    assert skipped.count(routine.INTRADAY) == 2  # 09:35 / 10:05 宕机错过
    assert done == [routine.INTRADAY]            # 只有 10:35 准点执行
    statuses = {r["ts"]: r["status"] for r in db.rows("SELECT ts,status FROM wakes")}
    assert statuses[dt.datetime.combine(TRADING_DAY, dt.time(9, 35)).isoformat()] == "skipped"


def test_wake_failure_recorded():
    def boom(scene, planned, wake_id):
        raise RuntimeError("llm down")

    hb = Heartbeat("leek-01", on_wake=boom, lookback_days=0)
    now = dt.datetime.combine(TRADING_DAY, dt.time(9, 35, 30))
    fired = hb.tick(now)
    assert (routine.INTRADAY, "failed") in fired
    r = db.one("SELECT detail FROM wakes WHERE status='failed'")
    assert "llm down" in r["detail"]
