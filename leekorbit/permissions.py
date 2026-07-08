"""交易权限成长线：创业板解锁条件检测（模拟券商开通流程）。

条件（platform.yaml 可配）：开户满 N 个交易日（以已结算净值天数计）且近 20 日日均资产
不低于门槛。满足即落一条 permission_unlocked 事件——事件本身是实验观察点：
韭菜拿到 20cm 权限后行为会不会变得更赌。
"""
from __future__ import annotations

import json
import logging

from . import db
from .tools import current_permissions

log = logging.getLogger(__name__)

DEFAULTS = {"chinext_min_days": 20, "chinext_min_avg_assets": 100000}


def check_chinext_unlock(agent: str, persona: dict, cfg: dict | None = None) -> bool:
    """在每日结算后调用；满足条件时解锁并通知（幂等）。返回本次是否发生解锁。"""
    c = {**DEFAULTS, **(cfg or {})}
    if "chinext" in current_permissions(agent, persona):
        return False
    rows = db.rows("SELECT nav FROM nav WHERE agent=? ORDER BY date", agent)
    if len(rows) < c["chinext_min_days"]:
        return False
    recent = [r["nav"] for r in rows[-20:]]
    avg = sum(recent) / len(recent)
    if avg < c["chinext_min_avg_assets"]:
        return False
    import datetime as dt

    payload = {
        "board": "chinext",
        "message": (
            "【券商通知】尊敬的客户：您的账户已满足创业板开通条件"
            f"（开户满{c['chinext_min_days']}个交易日且近20日日均资产达标），"
            "创业板交易权限已为您开通，即日起可交易300/301开头证券（涨跌幅20%）。"
        ),
    }
    db.insert("events", ts=dt.datetime.now().isoformat(), agent=agent,
              type="permission_unlocked", payload=json.dumps(payload, ensure_ascii=False))
    log.info("chinext unlocked for %s (avg assets %.0f)", agent, avg)
    return True
