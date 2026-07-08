"""常驻进程：心跳调度 +（后续）仪表盘。"""
from __future__ import annotations

import logging

from . import config
from .scheduler import Heartbeat

log = logging.getLogger(__name__)


def build_heartbeat() -> Heartbeat:
    persona = config.load_persona()
    on_wake = None
    try:
        from .wake import make_wake_handler

        on_wake = make_wake_handler(persona)
    except ImportError:
        log.info("wake handler not available yet; running heartbeat-only")
    return Heartbeat(agent=persona["name"], routine_cfg=persona.get("routine"), on_wake=on_wake)


def run_daemon() -> None:
    hb = build_heartbeat()
    try:
        from .dashboard.app import serve_in_thread

        serve_in_thread()
    except ImportError:
        log.info("dashboard not available yet")
    hb.run_forever()
