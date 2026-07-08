"""手动触发一次唤醒（#5 验收工具）。"""
from __future__ import annotations

import datetime as dt
import json

from . import config, db
from .scheduler import Heartbeat
from .wake import run_wake


def cmd_wake(args) -> None:
    persona = config.load_persona(args.agent)
    now = dt.datetime.now()
    wake_id = db.insert("wakes", ts=now.isoformat(), agent=persona["name"],
                        scene=args.scene, status="running", detail="manual")
    detail = run_wake(persona, args.scene, now, wake_id)
    db.conn().execute("UPDATE wakes SET status='done', detail=? WHERE id=?", (detail, wake_id))
    db.conn().commit()
    print(f"wake #{wake_id} done: {detail}")


def cmd_snapshot(args) -> None:
    r = db.one("SELECT * FROM snapshots WHERE id=?", args.id) if args.id else \
        db.one("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1")
    if not r:
        print("没有快照")
        return
    print(f"=== 快照 #{r['id']}  {r['ts']}  {r['agent']}  {r['scene']}  cost={r['cost']}")
    print("--- 信息流 ---")
    print(r["feed"])
    print("--- 会话 ---")
    for phase in json.loads(r["transcript"]):
        print(f"[{phase['tier']}]")
        for m in phase["messages"]:
            if m["role"] == "system":
                continue
            content = (m.get("content") or "")[:500]
            calls = "; ".join(f"{c['function']['name']}({c['function']['arguments']})"
                              for c in m.get("tool_calls", []))
            print(f"  {m['role']}: {content}" + (f"  >> {calls}" if calls else ""))
    print("--- 动作 ---")
    for a in json.loads(r["actions"]):
        print(f"  {a['tool']}({json.dumps(a['args'], ensure_ascii=False)}) -> {str(a['result'])[:200]}")


def register(sub) -> None:
    sp = sub.add_parser("wake", help="立即触发一次唤醒")
    sp.add_argument("scene", nargs="?", default="intraday",
                    choices=["premarket", "intraday", "lunch", "close_review", "evening", "weekend"])
    sp.add_argument("--agent", default="leek-01")
    sp.set_defaults(func=cmd_wake)

    register_diary(sub)
    register_costs(sub)

    sp = sub.add_parser("snapshot", help="回放决策快照（缺省最新一条）")
    sp.add_argument("id", nargs="?", type=int, default=None)
    sp.set_defaults(func=cmd_snapshot)


def cmd_diary(args) -> None:
    from . import memory

    for e in memory.recent(args.agent, args.limit):
        print(f"[{e['ts'][:16]} {e['scene']}] {e['content']}")


def register_diary(sub) -> None:
    sp = sub.add_parser("diary", help="按时间读韭菜日记")
    sp.add_argument("--agent", default="leek-01")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_diary)


def cmd_costs(args) -> None:
    from . import db

    print("日期        调用数  prompt_tok  compl_tok   成本(元)")
    for r in db.rows(
        "SELECT substr(ts,1,10) d, COUNT(*) n, SUM(prompt_tokens) pt, "
        "SUM(completion_tokens) ct, SUM(cost_est) c FROM llm_calls "
        "GROUP BY d ORDER BY d DESC LIMIT ?", args.limit
    ):
        print(f"{r['d']}  {r['n']:>5}  {r['pt'] or 0:>10}  {r['ct'] or 0:>9}  {r['c'] or 0:>9.4f}")


def register_costs(sub) -> None:
    sp = sub.add_parser("costs", help="LLM 成本按日汇总")
    sp.add_argument("--limit", type=int, default=31)
    sp.set_defaults(func=cmd_costs)
