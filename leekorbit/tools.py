"""动作空间（Action Space）：Agent 在一次唤醒中被允许的全部主动动作。

这里与 feed 一起构成 Agent 读写世界的唯一通道（ADR-0002 靠此收口）。
"""
from __future__ import annotations

import datetime as dt
import json

from . import datafeed, db, exchange, memory

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_stock",
            "description": "查看一只股票的详情：实时行情、近期K线、新闻、股吧千股千评。每次醒来最多查几只，注意力有限。",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "6位股票代码"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": "提交股票委托单。买入必须是100股整数倍。不填 limit_price 则按市价成交。",
            "parameters": {
                "type": "object",
                "properties": {
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "symbol": {"type": "string", "description": "6位股票代码"},
                    "qty": {"type": "integer", "description": "股数"},
                    "limit_price": {"type": "number", "description": "限价（可选）"},
                },
                "required": ["side", "symbol", "qty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_diary",
            "description": "在自己的炒股笔记本上记一笔想法。以后翻手机时能看到自己写过的笔记。",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
]

ESCALATE = "__escalate__"


def current_permissions(agent: str, persona: dict) -> tuple[str, ...]:
    perms = list(persona.get("permissions", ["main_board"]))
    for r in db.rows("SELECT payload FROM events WHERE agent=? AND type='permission_unlocked'", agent):
        try:
            perms.append(json.loads(r["payload"])["board"])
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return tuple(dict.fromkeys(perms))


class ActionSpace:
    def __init__(self, persona: dict, scene: str, now: dt.datetime,
                 lookup_limit: int = 5, fee_cfg: dict | None = None,
                 intercept_trades: bool = False):
        self.persona = persona
        self.agent = persona["name"]
        self.scene = scene
        self.now = now
        self.lookup_limit = lookup_limit
        self.fee_cfg = fee_cfg or {}
        self.intercept_trades = intercept_trades
        self.lookups_used = 0
        self.records: list[dict] = []   # 已执行动作及结果（进快照）
        self.escalated = False

    def dispatch(self, name: str, args: dict) -> str:
        try:
            handler = getattr(self, f"_do_{name}", None)
            if handler is None:
                result = f"没有这个操作：{name}"
            else:
                result = handler(**args)
        except TypeError as e:
            result = f"参数不对：{e}"
        self.records.append({"tool": name, "args": args, "result": result})
        return result

    # -- handlers -------------------------------------------------------------

    def _do_lookup_stock(self, symbol: str) -> str:
        if self.lookups_used >= self.lookup_limit:
            return "今天翻手机翻太久了，没时间再查了（本次查询次数已用完）。"
        self.lookups_used += 1
        name = datafeed.stock_name(symbol) or symbol
        q = datafeed.quote(symbol)
        if q is None:
            return f"{name}({symbol}) 行情加载失败，过会儿再看吧。"
        lines = [
            f"{name}({symbol})  现价 {q['last']:.2f} ({q['pct']:+.2f}%)  "
            f"今开 {q['open']:.2f} 最高 {q['high']:.2f} 最低 {q['low']:.2f}  "
            f"昨收 {q['prev_close']:.2f}  涨停 {q['limit_up']:.2f} 跌停 {q['limit_down']:.2f}"
        ]
        kline = datafeed.daily_kline(symbol, 10)
        if kline:
            recent = "  ".join(f"{k['date'][5:]}:{k['pct']:+.1f}%" for k in kline[-10:])
            lines.append(f"近10日涨跌：{recent}")
        news = datafeed.stock_news(symbol, 3)
        if news:
            lines.append("最近新闻：" + "；".join(n["title"] for n in news))
        cm = datafeed.stock_comment(symbol)
        if cm:
            lines.append(
                f"千股千评：综合得分{cm['score']:.1f}，全市场排名第{cm['rank']}，"
                f"关注指数{cm['attention']:.1f}，机构参与度{cm['org_participation']:.1f}%"
            )
        return "\n".join(lines)

    def _do_place_order(self, side: str, symbol: str, qty: int, limit_price: float | None = None) -> str:
        if self.intercept_trades:
            self.escalated = True
            return ESCALATE
        q = datafeed.quote(symbol)
        if q is None:
            return f"下单失败：{symbol} 行情加载不出来，交易软件转圈中。"
        name = datafeed.stock_name(symbol) or symbol
        quote = exchange.Quote(symbol=symbol, name=name, last=q["last"], prev_close=q["prev_close"],
                               limit_up=q["limit_up"], limit_down=q["limit_down"])
        try:
            fill = exchange.place_order(
                self.agent, side, qty, quote, limit_price=limit_price,
                permissions=current_permissions(self.agent, self.persona),
                now=self.now, fee_cfg=self.fee_cfg,
            )
        except exchange.OrderRejected as e:
            return f"委托被拒：{e}"
        act = "买入" if side == "buy" else "卖出"
        return (
            f"成交回执：{act} {fill.name}({fill.symbol}) {fill.qty}股 @ {fill.price:.2f}，"
            f"手续费合计 {fill.commission + fill.stamp_tax + fill.transfer_fee:.2f} 元，"
            f"账户现金变动 {fill.cash_delta:+.2f} 元。当前可用资金 {exchange.cash(self.agent):,.2f} 元。"
        )

    def _do_write_diary(self, content: str) -> str:
        memory.write(self.agent, self.scene, content, self.now)
        return "已记下。"
