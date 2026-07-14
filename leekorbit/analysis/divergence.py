"""日记 vs 账本差值：主观记述与客观事实的逐日对齐与偏差量化（ADR-0002 的观察工具）。

规则版情绪词表，不调 LLM；纯只读。核心指标：
- 亏损日 vs 盈利日的写日记率（坏日子会不会沉默）
- 亏损单 vs 盈利单的日记提及率（选择性记录）
- 日记情绪分与当日盈亏的方向一致性
"""
from __future__ import annotations

from collections import defaultdict

from .. import db
from .disposition import closed_lots

POS_WORDS = ["赚", "涨", "红", "开心", "稳", "不错", "满意", "冲", "起飞", "回本"]
NEG_WORDS = ["亏", "跌", "绿", "套", "割", "难受", "后悔", "完了", "被埋", "心态", "睡不着"]


def _sentiment(text: str) -> int:
    return sum(text.count(w) for w in POS_WORDS) - sum(text.count(w) for w in NEG_WORDS)


def daily_alignment(agent: str) -> list[dict]:
    """逐日对齐表：客观事实（成交/平仓盈亏/净值变动）× 主观日记。"""
    lots = closed_lots(agent)
    realized = defaultdict(float)
    for lot in lots:
        realized[lot["sell_date"]] += lot["pnl"]

    traded = defaultdict(set)
    names: dict[str, str] = {}
    for r in db.rows("SELECT ts, symbol, name FROM ledger WHERE agent=? AND kind IN ('buy','sell')", agent):
        traded[r["ts"][:10]].add(r["symbol"])
        if r["name"]:
            names[r["symbol"]] = r["name"]

    diary = defaultdict(list)
    for r in db.rows("SELECT ts, content FROM diary WHERE agent=?", agent):
        diary[r["ts"][:10]].append(r["content"])

    nav_rows = db.rows("SELECT date, nav FROM nav WHERE agent=? ORDER BY date", agent)
    nav_change = {}
    for prev, cur in zip(nav_rows, nav_rows[1:]):
        nav_change[cur["date"]] = round(cur["nav"] - prev["nav"], 2)

    days = sorted(set(realized) | set(traded) | set(diary) | set(nav_change))
    out = []
    for d in days:
        text = "\n".join(diary.get(d, []))
        mentioned = {s for s in traded.get(d, set()) if s in text or names.get(s, "") and names[s] in text}
        out.append({
            "date": d,
            "nav_change": nav_change.get(d),
            "realized_pnl": round(realized.get(d, 0.0), 2),
            "traded": sorted(traded.get(d, set())),
            "diary_written": bool(diary.get(d)),
            "mentioned": sorted(mentioned),
            "sentiment": _sentiment(text) if text else None,
        })
    return out


def _rate(rows: list[dict], key) -> float | None:
    return round(sum(1 for r in rows if key(r)) / len(rows), 2) if rows else None


def report(agent: str) -> dict:
    rows = daily_alignment(agent)

    def pnl_of(r):
        return r["nav_change"] if r["nav_change"] is not None else r["realized_pnl"]

    loss_days = [r for r in rows if pnl_of(r) < 0]
    gain_days = [r for r in rows if pnl_of(r) > 0]

    # 选择性记录：平仓当天日记有没有提这只票
    diary_by_day = {r["date"]: r for r in rows}
    lot_mentions = {"winners": [], "losers": []}
    for lot in closed_lots(agent):
        day = diary_by_day.get(lot["sell_date"])
        mentioned = bool(day and lot["symbol"] in day["mentioned"])
        lot_mentions["winners" if lot["pnl"] > 0 else "losers"].append(mentioned)

    def mention_rate(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    sentiments = [(pnl_of(r), r["sentiment"]) for r in rows if r["sentiment"] is not None and pnl_of(r) != 0]
    consistent = [1 if (p > 0) == (s > 0) else 0 for p, s in sentiments if s != 0]

    return {
        "days_analyzed": len(rows),
        "diary_rate": {
            "on_loss_days": _rate(loss_days, lambda r: r["diary_written"]),
            "on_gain_days": _rate(gain_days, lambda r: r["diary_written"]),
        },
        "closed_lot_mention_rate": {
            "winners": mention_rate(lot_mentions["winners"]),
            "losers": mention_rate(lot_mentions["losers"]),
        },
        "sentiment_pnl_consistency": round(sum(consistent) / len(consistent), 2) if consistent else None,
        "daily": rows,
    }


def main(agent: str = "leek-01") -> None:
    import json

    print(json.dumps(report(agent), ensure_ascii=False, indent=2))
