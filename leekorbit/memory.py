"""主观日记（Diary）：Agent 唯一能回忆的历史（ADR-0002）。

平台只做原样存取，绝不加工、绝不补充客观数据。
"""
from __future__ import annotations

import datetime as dt

from . import db


def write(agent: str, scene: str, content: str, ts: dt.datetime | None = None) -> int:
    ts = ts or dt.datetime.now()
    return db.insert("diary", ts=ts.isoformat(), agent=agent, scene=scene, content=content.strip())


def recent(agent: str, limit: int = 5) -> list[dict]:
    rows = db.rows(
        "SELECT ts, scene, content FROM diary WHERE agent=? ORDER BY id DESC LIMIT ?", agent, limit
    )
    return [dict(r) for r in reversed(rows)]
