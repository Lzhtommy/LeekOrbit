"""常驻进程：心跳调度 +（后续）仪表盘。"""
from __future__ import annotations

import logging

from . import config
from .scheduler import Heartbeat

log = logging.getLogger(__name__)


def build_heartbeat() -> Heartbeat:
    persona = config.load_persona()
    from . import nav, routine
    from .wake import make_wake_handler

    inner = make_wake_handler(persona)

    def on_wake(scene: str, planned, wake_id: int) -> str:
        try:
            return inner(scene, planned, wake_id)
        finally:
            if scene == routine.CLOSE_REVIEW:  # 收盘复盘时点顺带结算当日净值
                try:
                    nav.settle(persona["name"], planned.date())
                except Exception:
                    log.exception("nav settle failed")

    return Heartbeat(agent=persona["name"], routine_cfg=persona.get("routine"), on_wake=on_wake)


def run_daemon() -> None:
    hb = build_heartbeat()
    try:
        from .dashboard.app import serve_in_thread

        serve_in_thread()
    except ImportError:
        log.info("dashboard not available yet")
    hb.run_forever()
