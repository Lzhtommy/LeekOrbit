"""模拟交易所（Simulated Exchange）：A股核心规则，账本是唯一事实。

规则范围（见 docs/PLAN.md）：T+1、涨跌停封板拒单、100股整手、佣金/印花税/过户费、
可用资金约束、交易权限。按传入的行情快照价成交，不模拟盘口深度；限价单当次快照
不满足即废单，不留挂单。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import db

DEFAULT_FEES = {
    "commission_rate": 0.00025,
    "commission_min": 5.0,
    "stamp_tax_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
}

# 板块 → (代码前缀, 涨跌幅, 所需权限)
_BOARDS = {
    "main_board": (("60", "000", "001", "002", "003"), 0.10),
    "chinext": (("300", "301", "302"), 0.20),
    "star": (("688", "689"), 0.20),
    "bse": (("82", "83", "87", "88", "43", "92"), 0.30),
}


class OrderRejected(Exception):
    """拒单，message 即拒因（会原样反馈给 Agent）。"""


@dataclass
class Quote:
    symbol: str
    name: str
    last: float
    prev_close: float
    limit_up: float | None = None
    limit_down: float | None = None

    def __post_init__(self):
        ratio = limit_ratio(self.symbol, self.name)
        if self.limit_up is None:
            self.limit_up = round(self.prev_close * (1 + ratio), 2)
        if self.limit_down is None:
            self.limit_down = round(self.prev_close * (1 - ratio), 2)


@dataclass
class Fill:
    side: str
    symbol: str
    name: str
    qty: int
    price: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    cash_delta: float


def board_of(symbol: str) -> str:
    for board, (prefixes, _) in _BOARDS.items():
        if symbol.startswith(prefixes):
            return board
    raise OrderRejected(f"无法识别的证券代码 {symbol}")


def limit_ratio(symbol: str, name: str = "") -> float:
    board = board_of(symbol)
    ratio = _BOARDS[board][1]
    if board == "main_board" and "ST" in name.upper():
        ratio = 0.05
    return ratio


# -- 账户推导（一切来自账本） -------------------------------------------------

def cash(agent: str) -> float:
    r = db.one("SELECT COALESCE(SUM(amount), 0) AS c FROM ledger WHERE agent=?", agent)
    return round(r["c"], 2)


def positions(agent: str) -> dict[str, dict]:
    """{symbol: {qty, name, cost}}，cost 为买入摊薄成本（含费用，卖出按比例减）。"""
    pos: dict[str, dict] = {}
    for r in db.rows(
        "SELECT kind, symbol, name, qty, price, commission, stamp_tax, transfer_fee "
        "FROM ledger WHERE agent=? AND kind IN ('buy','sell') ORDER BY id", agent
    ):
        p = pos.setdefault(r["symbol"], {"qty": 0, "name": r["name"], "cost": 0.0})
        fees = r["commission"] + r["stamp_tax"] + r["transfer_fee"]
        if r["kind"] == "buy":
            p["qty"] += r["qty"]
            p["cost"] += r["qty"] * r["price"] + fees
        else:
            frac = r["qty"] / p["qty"] if p["qty"] else 0
            p["cost"] -= p["cost"] * frac
            p["qty"] -= r["qty"]
        p["name"] = r["name"] or p["name"]
    return {s: p for s, p in pos.items() if p["qty"] > 0}


def bought_today(agent: str, symbol: str, today: dt.date) -> int:
    r = db.one(
        "SELECT COALESCE(SUM(qty),0) AS q FROM ledger "
        "WHERE agent=? AND symbol=? AND kind='buy' AND ts LIKE ?",
        agent, symbol, f"{today.isoformat()}%",
    )
    return r["q"]


def sellable(agent: str, symbol: str, today: dt.date) -> int:
    total = positions(agent).get(symbol, {}).get("qty", 0)
    return max(0, total - bought_today(agent, symbol, today))


def deposit(agent: str, amount: float, ts: dt.datetime, note: str = "入金") -> None:
    db.insert("ledger", ts=ts.isoformat(), agent=agent, kind="deposit", amount=amount, note=note)


def ensure_seeded(agent: str, capital: float, ts: dt.datetime | None = None) -> None:
    if db.one("SELECT id FROM ledger WHERE agent=? AND kind='deposit'", agent) is None:
        deposit(agent, capital, ts or dt.datetime.now(), note="初始本金")


# -- 撮合 ---------------------------------------------------------------------

def _fees(side: str, amount: float, cfg: dict) -> tuple[float, float, float]:
    c = {**DEFAULT_FEES, **cfg}
    commission = round(max(c["commission_min"], amount * c["commission_rate"]), 2)
    stamp = round(amount * c["stamp_tax_rate"], 2) if side == "sell" else 0.0
    transfer = round(amount * c["transfer_fee_rate"], 2)
    return commission, stamp, transfer


def place_order(
    agent: str,
    side: str,                    # buy / sell
    qty: int,
    quote: Quote,
    limit_price: float | None = None,   # None = 市价
    permissions: tuple[str, ...] = ("main_board",),
    now: dt.datetime | None = None,
    fee_cfg: dict | None = None,
) -> Fill:
    now = now or dt.datetime.now()
    symbol, name = quote.symbol, quote.name
    board = board_of(symbol)
    if board not in permissions:
        label = {"chinext": "创业板", "star": "科创板", "bse": "北交所"}.get(board, board)
        raise OrderRejected(f"账户未开通{label}交易权限，无法交易 {symbol}")
    if qty <= 0:
        raise OrderRejected("委托数量必须为正")

    held = positions(agent).get(symbol, {}).get("qty", 0)
    if side == "buy" and qty % 100 != 0:
        raise OrderRejected("买入数量必须为100股整数倍")
    if side == "sell" and qty % 100 != 0 and qty != held:
        raise OrderRejected("卖出数量必须为100股整数倍（零股尾仓需一次性卖出）")

    if limit_price is not None and not (quote.limit_down <= limit_price <= quote.limit_up):
        raise OrderRejected(
            f"委托价 {limit_price} 超出当日涨跌停价范围 [{quote.limit_down}, {quote.limit_up}]"
        )

    # 涨跌停封板不可成交
    if side == "buy" and quote.last >= quote.limit_up:
        raise OrderRejected(f"{name} 已封涨停板（{quote.limit_up}），买单无法成交")
    if side == "sell" and quote.last <= quote.limit_down:
        raise OrderRejected(f"{name} 已封跌停板（{quote.limit_down}），卖单无法成交")

    # 限价单：当次快照价不满足即废单
    fill_price = quote.last
    if limit_price is not None:
        if side == "buy" and limit_price < fill_price:
            raise OrderRejected(f"限价 {limit_price} 低于现价 {fill_price}，本次未成交（废单）")
        if side == "sell" and limit_price > fill_price:
            raise OrderRejected(f"限价 {limit_price} 高于现价 {fill_price}，本次未成交（废单）")

    amount = round(qty * fill_price, 2)
    commission, stamp, transfer = _fees(side, amount, fee_cfg or {})

    if side == "buy":
        total = round(amount + commission + stamp + transfer, 2)
        available = cash(agent)
        if total > available:
            raise OrderRejected(f"可用资金不足：需要 {total:.2f} 元，可用 {available:.2f} 元")
        cash_delta = -total
    elif side == "sell":
        ok = sellable(agent, symbol, now.date())
        if qty > ok:
            raise OrderRejected(
                f"可卖数量不足：持有 {held} 股，其中当日买入部分 T+1 次日方可卖出（今日可卖 {ok} 股）"
            )
        cash_delta = round(amount - commission - stamp - transfer, 2)
    else:
        raise OrderRejected(f"未知方向 {side}")

    db.insert(
        "ledger", ts=now.isoformat(), agent=agent, kind=side, symbol=symbol, name=name,
        qty=qty, price=fill_price, amount=cash_delta,
        commission=commission, stamp_tax=stamp, transfer_fee=transfer,
    )
    return Fill(side, symbol, name, qty, fill_price, commission, stamp, transfer, cash_delta)
