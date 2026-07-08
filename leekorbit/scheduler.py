"""心跳调度：每分钟对账「预期唤醒 vs 已落库唤醒」。

到点的唤醒在宽限期内触发；错过宽限期（宕机、重启）的一律标记 skipped 不补跑。
自己实现 tick 循环而非 cron 表达式，是为了让「错过即跳过」成为同一段逻辑的自然结果。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from . import db, routine

log = logging.getLogger(__name__)

GRACE = dt.timedelta(minutes=3)  # 到点后多久内仍算准点


class Heartbeat:
    def __init__(self, agent: str, routine_cfg: dict | None = None, on_wake=None, lookback_days: int = 2):
        self.agent = agent
        self.routine_cfg = routine_cfg
        self.on_wake = on_wake  # callable(scene, planned_ts) -> detail str；None 时纯心跳
        self.lookback_days = lookback_days
        self._stop = threading.Event()

    # -- core ---------------------------------------------------------------

    def tick(self, now: dt.datetime | None = None) -> list[tuple[str, str]]:
        """对账一次，返回本次落库的 [(scene, status)]。可直接在测试中调用。"""
        now = now or dt.datetime.now()
        fired: list[tuple[str, str]] = []
        for day_offset in range(self.lookback_days, -1, -1):
            date = now.date() - dt.timedelta(days=day_offset)
            for planned, scene in routine.expected_wakes(date, self.routine_cfg):
                if planned > now:
                    continue
                if self._recorded(planned):
                    continue
                if now - planned > GRACE:
                    self._record(planned, scene, "skipped", "missed while down")
                    fired.append((scene, "skipped"))
                    continue
                fired.append((scene, self._fire(planned, scene)))
        return fired

    def _recorded(self, planned: dt.datetime) -> bool:
        return db.one(
            "SELECT id FROM wakes WHERE agent=? AND ts=?", self.agent, planned.isoformat()
        ) is not None

    def _record(self, planned: dt.datetime, scene: str, status: str, detail: str) -> int:
        return db.insert(
            "wakes", ts=planned.isoformat(), agent=self.agent, scene=scene, status=status, detail=detail
        )

    def _fire(self, planned: dt.datetime, scene: str) -> str:
        wake_id = self._record(planned, scene, "running", None)
        try:
            detail = self.on_wake(scene, planned, wake_id) if self.on_wake else "heartbeat"
            status = "done"
        except Exception as e:
            log.exception("wake failed: %s %s", scene, planned)
            detail, status = f"{type(e).__name__}: {e}", "failed"
        db.conn().execute("UPDATE wakes SET status=?, detail=? WHERE id=?", (status, detail, wake_id))
        db.conn().commit()
        return status

    # -- daemon -------------------------------------------------------------

    def run_forever(self, interval: float = 60.0) -> None:
        log.info("heartbeat started for %s", self.agent)
        while not self._stop.is_set():
            try:
                for scene, status in self.tick():
                    log.info("wake %s -> %s", scene, status)
            except Exception:
                log.exception("tick crashed; continuing")
            self._stop.wait(interval)

    def stop(self) -> None:
        self._stop.set()
