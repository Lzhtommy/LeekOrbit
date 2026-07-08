"""一次唤醒（Wake）的完整会话：feed → LLM 工具循环 → 决策快照落库。

分层路由（ADR-0003）：看盘类场景由便宜层处理；便宜层一旦想下单，
本次会话升级到强层带着同一份信息流重新决策（下单只在强层执行）。
System prompt 只含人设背景与工具说明，无任何行为暗示（ADR-0001）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from . import config, db, exchange, feed, routine
from .llm import LLM
from .tools import ESCALATE, TOOL_DEFS, ActionSpace

log = logging.getLogger(__name__)

MAX_ROUNDS = 8
CHEAP_SCENES = {routine.PREMARKET, routine.INTRADAY, routine.LUNCH}

SYSTEM_TMPL = """你是{display_name}。

{background}

你有一个证券账户（{risk_profile}）。下面是你现在拿起手机看到的内容。
看完后你可以查股票详情（lookup_stock）、下委托单（place_order）、在自己的笔记本上记想法（write_diary），
也可以什么都不做——说说你此刻的想法，然后放下手机去干别的。
用第一人称、像平常自言自语那样表达。"""


def _system_prompt(persona: dict) -> str:
    return SYSTEM_TMPL.format(
        display_name=persona.get("display_name", persona["name"]),
        background=persona["background"].strip(),
        risk_profile=persona.get("risk_profile", ""),
    )


def _serialize(msg) -> dict:
    if isinstance(msg, dict):
        return msg
    out = {"role": msg.role, "content": msg.content}
    if getattr(msg, "tool_calls", None):
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return out


def _run_session(llm: LLM, tier: str, persona: dict, feed_text: str, space: ActionSpace,
                 wake_id: int | None) -> list[dict]:
    """跑一段工具调用循环，返回序列化消息列表。"""
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(persona)},
        {"role": "user", "content": feed_text},
    ]
    for _ in range(MAX_ROUNDS):
        msg = llm.complete(tier, messages, TOOL_DEFS, wake_id=wake_id)
        messages.append(_serialize(msg))
        if not getattr(msg, "tool_calls", None):
            break
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = space.dispatch(tc.function.name, args)
            if result == ESCALATE:
                result = "（这单要动真金白银，你决定先放下手机，认真想想再操作。）"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        if space.escalated:
            break
    return messages


def run_wake(persona: dict, scene: str, planned: dt.datetime, wake_id: int) -> str:
    agent = persona["name"]
    platform = config.load_platform()
    exchange.ensure_seeded(agent, persona["capital"], planned)

    feed_text = feed.build(agent, scene, planned,
                           diary_recall=platform.get("agent", {}).get("diary_recall", 5))
    llm = LLM(platform.get("llm"), agent=agent)
    lookup_limit = platform.get("agent", {}).get("lookup_limit", 5)
    fee_cfg = platform.get("exchange", {})

    tier = "cheap" if scene in CHEAP_SCENES else "strong"
    space = ActionSpace(persona, scene, planned, lookup_limit=lookup_limit,
                        fee_cfg=fee_cfg, intercept_trades=(tier == "cheap"))
    phases = [{"tier": tier, "messages": _run_session(llm, tier, persona, feed_text, space, wake_id)}]

    if space.escalated:
        strong_space = ActionSpace(persona, scene, planned, lookup_limit=lookup_limit,
                                   fee_cfg=fee_cfg, intercept_trades=False)
        phases.append({"tier": "strong",
                       "messages": _run_session(llm, "strong", persona, feed_text, strong_space, wake_id)})
        space.records += strong_space.records

    cost = db.one(
        "SELECT COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, "
        "COALESCE(SUM(cost_est),0) c FROM llm_calls WHERE wake_id=?", wake_id
    )
    db.insert(
        "snapshots", wake_id=wake_id, ts=planned.isoformat(), agent=agent, scene=scene,
        feed=feed_text,
        transcript=json.dumps(phases, ensure_ascii=False),
        actions=json.dumps(space.records, ensure_ascii=False),
        cost=json.dumps({"prompt_tokens": cost["pt"], "completion_tokens": cost["ct"],
                         "cost_cny": round(cost["c"], 4), "mock": llm.mock}, ensure_ascii=False),
    )

    executed = [r for r in space.records if r["tool"] == "place_order" and r["result"] != ESCALATE]
    trades = [r for r in executed if str(r["result"]).startswith("成交回执")]
    summary = f"{len(space.records)} actions, {len(trades)} trades" + (" [mock]" if llm.mock else "")
    log.info("wake %s %s: %s", agent, scene, summary)
    return summary


def make_wake_handler(persona: dict):
    def handler(scene: str, planned: dt.datetime, wake_id: int) -> str:
        return run_wake(persona, scene, planned, wake_id)

    return handler
