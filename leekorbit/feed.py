"""信息流（Feed）组装：按作息场景生成散户视角的推送内容。

这是 Agent 读取世界的唯一被动通道（另一通道是受限的主动查询工具）。
数据拿不到时如实标注「加载失败」，不编造（ADR 原则：环境可以缺失，不能造假）。
"""
from __future__ import annotations

import datetime as dt
import json

from . import datafeed, db, exchange, memory, routine

FAIL = "（加载失败，稍后再看）"


def _fmt_indices() -> str:
    idx = datafeed.indices()
    if idx is None:
        return f"【大盘】{FAIL}"
    parts = [f"{i['name']} {i['last']:.2f} ({i['pct']:+.2f}%)" for i in idx]
    breadth = datafeed.market_breadth()
    b = f"｜上涨{breadth['up']}家 下跌{breadth['down']}家 涨停{breadth['limit_up']} 跌停{breadth['limit_down']}" if breadth else ""
    return "【大盘】" + "  ".join(parts) + b


def _fmt_positions(agent: str) -> str:
    pos = exchange.positions(agent)
    cash = exchange.cash(agent)
    if not pos:
        return f"【我的账户】空仓，可用资金 {cash:,.2f} 元"
    lines = [f"【我的账户】可用资金 {cash:,.2f} 元，持仓："]
    for sym, p in pos.items():
        avg = p["cost"] / p["qty"]
        q = datafeed.quote(sym)
        if q:
            pnl = (q["last"] - avg) * p["qty"]
            pct = (q["last"] / avg - 1) * 100
            lines.append(
                f"  {sym} {p['name']} {p['qty']}股  现价{q['last']:.2f} ({q['pct']:+.2f}%)  "
                f"成本{avg:.3f}  浮动盈亏 {pnl:+,.0f}元 ({pct:+.2f}%)"
            )
        else:
            lines.append(f"  {sym} {p['name']} {p['qty']}股  成本{avg:.3f}  行情{FAIL}")
    return "\n".join(lines)


def _fmt_hot() -> str:
    hot = datafeed.hot_rank(10)
    if hot is None:
        return f"【股吧人气榜】{FAIL}"
    lines = ["【股吧人气榜】大家都在看："]
    lines += [f"  {h['rank']}. {h['name']}({h['symbol']}) {h['last']:.2f} ({h['pct']:+.2f}%)" for h in hot]
    up = datafeed.hot_up(5)
    if up:
        lines.append("【人气飙升】" + "  ".join(f"{h['name']}({h['pct']:+.2f}%)" for h in up))
    return "\n".join(lines)


def _fmt_holding_news(agent: str) -> str:
    pos = exchange.positions(agent)
    if not pos:
        return ""
    lines = []
    for sym, p in list(pos.items())[:3]:
        news = datafeed.stock_news(sym, 3)
        if news:
            lines.append(f"【{p['name']}新闻】" + "；".join(n["title"] for n in news))
    return "\n".join(lines)


def _fmt_diary(agent: str, limit: int = 5) -> str:
    entries = memory.recent(agent, limit)
    if not entries:
        return ""
    lines = ["【我之前的笔记】"]
    lines += [f"  {e['ts'][:16]} {e['content']}" for e in entries]
    return "\n".join(lines)


def _fmt_today_fills(agent: str, today: dt.date) -> str:
    rows = db.rows(
        "SELECT kind, symbol, name, qty, price FROM ledger "
        "WHERE agent=? AND kind IN ('buy','sell') AND ts LIKE ? ORDER BY id",
        agent, f"{today.isoformat()}%",
    )
    if not rows:
        return "【今日成交】无"
    lines = ["【今日成交】"]  # 券商App当日成交单，属于环境而非记忆
    lines += [
        f"  {'买入' if r['kind'] == 'buy' else '卖出'} {r['symbol']} {r['name']} {r['qty']}股 @ {r['price']}"
        for r in rows
    ]
    return "\n".join(lines)


def _fmt_notices(agent: str) -> str:
    rows = db.rows("SELECT id, ts, type, payload FROM events WHERE agent=? AND notified=0", agent)
    if not rows:
        return ""
    lines = ["【券商通知】"]
    for r in rows:
        try:
            msg = json.loads(r["payload"]).get("message", r["payload"])
        except (json.JSONDecodeError, TypeError, AttributeError):
            msg = r["payload"]
        lines.append(f"  {msg}")
        db.conn().execute("UPDATE events SET notified=1 WHERE id=?", (r["id"],))
    db.conn().commit()
    return "\n".join(lines)


SCENE_LABEL = {
    routine.PREMARKET: "盘前（上班路上刷手机）",
    routine.INTRADAY: "盘中（工位上偷瞄行情）",
    routine.LUNCH: "午休（刷刷股吧热榜）",
    routine.CLOSE_REVIEW: "收盘（看看今天战果）",
    routine.EVENING: "晚间（复盘，记点笔记）",
    routine.WEEKEND: "周末/休市（随便翻翻）",
}


def build(agent: str, scene: str, now: dt.datetime | None = None, diary_recall: int = 5) -> str:
    now = now or dt.datetime.now()
    blocks: list[str] = [f"时间：{now.strftime('%Y-%m-%d %H:%M')} {SCENE_LABEL.get(scene, scene)}"]
    blocks.append(_fmt_notices(agent))
    blocks.append(_fmt_positions(agent))

    if scene == routine.PREMARKET:
        blocks += [_fmt_holding_news(agent), _fmt_hot(), _fmt_diary(agent, diary_recall)]
    elif scene == routine.INTRADAY:
        blocks += [_fmt_indices(), _fmt_hot()]
    elif scene == routine.LUNCH:
        blocks += [_fmt_indices(), _fmt_hot(), _fmt_holding_news(agent)]
    elif scene == routine.CLOSE_REVIEW:
        blocks += [_fmt_indices(), _fmt_today_fills(agent, now.date()), _fmt_hot()]
    elif scene == routine.EVENING:
        blocks += [
            _fmt_indices(), _fmt_today_fills(agent, now.date()), _fmt_holding_news(agent),
            _fmt_hot(), _fmt_diary(agent, diary_recall),
        ]
    elif scene == routine.WEEKEND:
        blocks += [_fmt_hot(), _fmt_holding_news(agent), _fmt_diary(agent, diary_recall)]

    return "\n\n".join(b for b in blocks if b)
