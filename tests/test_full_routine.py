"""#8 验收：完整交易日 + 周末的唤醒序列仿真。"""
import datetime as dt

import pytest

from leekorbit import db, routine, trade_calendar
from leekorbit.scheduler import Heartbeat

TRADING_DAY = dt.date(2026, 7, 8)
SATURDAY = dt.date(2026, 7, 11)


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch):
    monkeypatch.setattr(trade_calendar, "is_trading_day", lambda d=None: d.weekday() < 5)


def simulate_day(date, recorder):
    hb = Heartbeat("leek-01", on_wake=recorder, lookback_days=0)
    t = dt.datetime.combine(date, dt.time(0, 0))
    end = dt.datetime.combine(date, dt.time(23, 59))
    while t <= end:
        hb.tick(t)
        t += dt.timedelta(minutes=1)


def test_full_trading_day_sequence():
    seen = []

    def recorder(scene, planned, wake_id):
        seen.append((planned.time().isoformat(timespec="minutes"), scene))
        return "ok"

    simulate_day(TRADING_DAY, recorder)
    expected = [(t.time().isoformat(timespec="minutes"), s)
                for t, s in routine.expected_wakes(TRADING_DAY)]
    assert seen == expected                      # 时点与场景一一对应、无重复无遗漏
    assert seen[0] == ("09:10", routine.PREMARKET)
    assert ("12:00", routine.LUNCH) in seen
    assert seen[-1] == ("20:30", routine.EVENING)
    assert all(r["status"] == "done" for r in db.rows("SELECT status FROM wakes"))


def test_weekend_single_low_freq_wake():
    seen = []
    simulate_day(SATURDAY, lambda s, p, w: seen.append(s) or "ok")
    assert seen == [routine.WEEKEND]
