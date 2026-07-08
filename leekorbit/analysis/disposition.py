"""行为标注第一刀：处置效应（赚了就跑、亏了死扛）的最粗信号。

只读客观账本（Ledger），FIFO 配对平仓批次，统计盈利单 vs 亏损单的持有时长。
不写库、不触碰主观日记。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .. import db


def _day(ts: str) -> dt.date:
    return dt.date.fromisoformat(ts[:10])


def closed_lots(agent: str) -> list[dict]:
    """FIFO 配对的已平仓批次：{symbol, qty, buy_date, sell_date, holding_days, pnl}。"""
    open_lots: dict[str, list[dict]] = defaultdict(list)
    closed: list[dict] = []
    for r in db.rows(
        "SELECT ts, kind, symbol, qty, price, commission, stamp_tax, transfer_fee "
        "FROM ledger WHERE agent=? AND kind IN ('buy','sell') ORDER BY id", agent
    ):
        fees_per_share = (r["commission"] + r["stamp_tax"] + r["transfer_fee"]) / r["qty"]
        if r["kind"] == "buy":
            open_lots[r["symbol"]].append(
                {"date": _day(r["ts"]), "qty": r["qty"], "cost": r["price"] + fees_per_share})
            continue
        remaining = r["qty"]
        sell_net = r["price"] - fees_per_share
        while remaining > 0 and open_lots[r["symbol"]]:
            lot = open_lots[r["symbol"]][0]
            take = min(remaining, lot["qty"])
            closed.append({
                "symbol": r["symbol"], "qty": take,
                "buy_date": lot["date"].isoformat(), "sell_date": _day(r["ts"]).isoformat(),
                "holding_days": (_day(r["ts"]) - lot["date"]).days,
                "pnl": round((sell_net - lot["cost"]) * take, 2),
            })
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] == 0:
                open_lots[r["symbol"]].pop(0)
    return closed


def open_holding_days(agent: str, asof: dt.date | None = None) -> list[int]:
    asof = asof or dt.date.today()
    days: list[int] = []
    lots: dict[str, list[dict]] = defaultdict(list)
    for r in db.rows(
        "SELECT ts, kind, symbol, qty FROM ledger WHERE agent=? AND kind IN ('buy','sell') ORDER BY id",
        agent,
    ):
        if r["kind"] == "buy":
            lots[r["symbol"]].append({"date": _day(r["ts"]), "qty": r["qty"]})
        else:
            remaining = r["qty"]
            while remaining > 0 and lots[r["symbol"]]:
                lot = lots[r["symbol"]][0]
                take = min(remaining, lot["qty"])
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] == 0:
                    lots[r["symbol"]].pop(0)
    for sym_lots in lots.values():
        days += [(asof - lot["date"]).days for lot in sym_lots if lot["qty"] > 0]
    return days


def report(agent: str, asof: dt.date | None = None) -> dict:
    closed = closed_lots(agent)
    winners = [c for c in closed if c["pnl"] > 0]
    losers = [c for c in closed if c["pnl"] < 0]

    def avg_days(lots):
        return round(sum(c["holding_days"] for c in lots) / len(lots), 1) if lots else None

    dist = defaultdict(int)
    for d in [c["holding_days"] for c in closed] + open_holding_days(agent, asof):
        bucket = "0-1天" if d <= 1 else "2-5天" if d <= 5 else "6-20天" if d <= 20 else ">20天"
        dist[bucket] += 1

    return {
        "closed_lots": len(closed),
        "winners": {"count": len(winners), "avg_holding_days": avg_days(winners),
                    "total_pnl": round(sum(c["pnl"] for c in winners), 2)},
        "losers": {"count": len(losers), "avg_holding_days": avg_days(losers),
                   "total_pnl": round(sum(c["pnl"] for c in losers), 2)},
        "holding_days_distribution": dict(dist),
        "disposition_signal": (
            None if not winners or not losers else
            round(avg_days(losers) / avg_days(winners), 2) if avg_days(winners) else None
        ),  # >1 越大越「拿不住盈利、扛得住亏损」
    }


def main(agent: str = "leek-01") -> None:
    import json

    print(json.dumps(report(agent), ensure_ascii=False, indent=2))
