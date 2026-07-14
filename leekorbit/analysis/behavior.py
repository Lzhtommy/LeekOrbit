"""行为标注（Behavior Annotation）：给客观账本里的每笔交易打行为金融学标签。

只读账本；价格数据源以 callable 注入，离线 fixture 可测。每个标签带可解释证据。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .. import db

CHASE_PCT = 5.0        # 追涨：买入当日该股涨幅阈值
AVG_DOWN_RATIO = 0.97  # 补仓：买价低于持仓成本的比例
PYRAMID_RATIO = 1.03   # 越涨越买：买价高于上次买价的比例
FAST_PROFIT_DAYS = 2   # 落袋过快：盈利平仓持有天数上限
HOLD_LOSER_DAYS = 10   # 死扛：浮亏持有天数下限
HOLD_LOSER_RATIO = 0.95

TAG_LABELS = {
    "chase_up": "追涨买入",
    "average_down": "补仓摊成本",
    "pyramid_up": "越涨越买",
    "cut_loss": "割肉离场",
    "take_profit_fast": "落袋过快",
    "hold_loser": "死扛浮亏",
}


def _day(ts: str) -> dt.date:
    return dt.date.fromisoformat(ts[:10])


def tag_trades(agent: str, day_pct=None, current_price=None, asof: dt.date | None = None) -> dict:
    """扫描账本产出标签事件流。

    day_pct(symbol, iso_date) -> 当日涨跌幅% | None；current_price(symbol) -> 现价 | None。
    两者缺省时对应标签静默跳过（不猜数据）。
    """
    asof = asof or dt.date.today()
    events: list[dict] = []
    lots: dict[str, list[dict]] = defaultdict(list)   # 未平仓批次
    last_buy: dict[str, float] = {}

    def emit(ts, symbol, name, tag, evidence):
        events.append({"ts": ts, "symbol": symbol, "name": name or symbol,
                       "tag": tag, "label": TAG_LABELS[tag], "evidence": evidence})

    for r in db.rows(
        "SELECT ts, kind, symbol, name, qty, price, commission, stamp_tax, transfer_fee "
        "FROM ledger WHERE agent=? AND kind IN ('buy','sell') ORDER BY id", agent
    ):
        sym, name, price, date = r["symbol"], r["name"], r["price"], _day(r["ts"])
        fees_ps = (r["commission"] + r["stamp_tax"] + r["transfer_fee"]) / r["qty"]

        if r["kind"] == "buy":
            pct = day_pct(sym, date.isoformat()) if day_pct else None
            if pct is not None and pct >= CHASE_PCT:
                emit(r["ts"], sym, name, "chase_up", f"买入当日该股上涨 {pct:+.1f}%")
            held_qty = sum(lot["qty"] for lot in lots[sym])
            if held_qty > 0:
                avg = sum(lot["qty"] * lot["cost"] for lot in lots[sym]) / held_qty
                if price <= avg * AVG_DOWN_RATIO:
                    emit(r["ts"], sym, name, "average_down",
                         f"持仓成本 {avg:.2f}，以 {price:.2f} 加仓摊低")
                elif sym in last_buy and price >= last_buy[sym] * PYRAMID_RATIO:
                    emit(r["ts"], sym, name, "pyramid_up",
                         f"上次买价 {last_buy[sym]:.2f}，本次 {price:.2f} 更高仍加仓")
            lots[sym].append({"date": date, "qty": r["qty"], "cost": price + fees_ps})
            last_buy[sym] = price
        else:
            remaining, sell_net = r["qty"], price - fees_ps
            while remaining > 0 and lots[sym]:
                lot = lots[sym][0]
                take = min(remaining, lot["qty"])
                pnl = round((sell_net - lot["cost"]) * take, 2)
                days = (date - lot["date"]).days
                if pnl < 0:
                    emit(r["ts"], sym, name, "cut_loss", f"亏损 {pnl:+.0f} 元离场（持有{days}天）")
                elif days <= FAST_PROFIT_DAYS:
                    emit(r["ts"], sym, name, "take_profit_fast",
                         f"盈利 {pnl:+.0f} 元，仅持有{days}天就卖出")
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] == 0:
                    lots[sym].pop(0)

    # 在持批次的死扛检测
    for sym, sym_lots in lots.items():
        cur = current_price(sym) if current_price else None
        if cur is None:
            continue
        for lot in sym_lots:
            if lot["qty"] <= 0:
                continue
            days = (asof - lot["date"]).days
            if days >= HOLD_LOSER_DAYS and cur < lot["cost"] * HOLD_LOSER_RATIO:
                pct = (cur / lot["cost"] - 1) * 100
                name = db.one("SELECT name FROM ledger WHERE symbol=? AND name IS NOT NULL "
                              "ORDER BY id DESC LIMIT 1", sym)
                emit(f"{asof.isoformat()}T00:00:00", sym, name["name"] if name else sym,
                     "hold_loser", f"浮亏 {pct:.1f}% 已持有{days}天未处理")

    summary = defaultdict(int)
    for e in events:
        summary[e["tag"]] += 1
    return {"events": events, "summary": dict(summary)}


def main(agent: str = "leek-01") -> None:
    import json

    from .. import datafeed

    def day_pct(symbol, iso_date):
        k = datafeed.daily_kline(symbol, 60)
        return next((row["pct"] for row in k or [] if row["date"] == iso_date), None)

    def current_price(symbol):
        q = datafeed.quote(symbol)
        return q["last"] if q else None

    print(json.dumps(tag_trades(agent, day_pct, current_price), ensure_ascii=False, indent=2))
