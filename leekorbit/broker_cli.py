"""人肉交易所 CLI：手动喂价下单，验证 A股规则（#2 验收工具）。"""
from __future__ import annotations

import datetime as dt

from . import config, exchange


def _quote(args) -> exchange.Quote:
    return exchange.Quote(
        symbol=args.symbol, name=args.name, last=args.price, prev_close=args.prev_close
    )


def cmd_trade(args) -> None:
    now = dt.datetime.fromisoformat(args.at) if args.at else dt.datetime.now()
    try:
        fill = exchange.place_order(
            args.agent, args.side, args.qty, _quote(args),
            limit_price=args.limit, permissions=tuple(args.perms.split(",")), now=now,
            fee_cfg=config.load_platform().get("exchange", {}),
        )
    except exchange.OrderRejected as e:
        print(f"拒单：{e}")
        raise SystemExit(1)
    print(
        f"成交：{fill.side} {fill.symbol} {fill.name} {fill.qty}股 @ {fill.price}  "
        f"佣金{fill.commission} 印花税{fill.stamp_tax} 过户费{fill.transfer_fee}  "
        f"现金变动 {fill.cash_delta:+.2f}"
    )


def cmd_account(args) -> None:
    print(f"可用资金: {exchange.cash(args.agent):.2f}")
    for sym, p in exchange.positions(args.agent).items():
        avg = p["cost"] / p["qty"] if p["qty"] else 0
        print(f"持仓: {sym} {p['name']} {p['qty']}股  摊薄成本 {avg:.3f}")


def cmd_deposit(args) -> None:
    exchange.deposit(args.agent, args.amount, dt.datetime.now())
    print(f"入金 {args.amount}，可用资金 {exchange.cash(args.agent):.2f}")


def register(sub) -> None:
    for side in ("buy", "sell"):
        sp = sub.add_parser(side, help=f"手动{'买入' if side == 'buy' else '卖出'}（人肉喂价）")
        sp.add_argument("symbol")
        sp.add_argument("qty", type=int)
        sp.add_argument("--price", type=float, required=True, help="当前快照价")
        sp.add_argument("--prev-close", type=float, required=True, dest="prev_close")
        sp.add_argument("--name", default="")
        sp.add_argument("--limit", type=float, default=None, help="限价（缺省为市价）")
        sp.add_argument("--agent", default="leek-01")
        sp.add_argument("--perms", default="main_board")
        sp.add_argument("--at", default=None, help="指定成交时间 ISO（测 T+1 用）")
        sp.set_defaults(func=cmd_trade, side=side)

    sp = sub.add_parser("account", help="查看资金与持仓")
    sp.add_argument("--agent", default="leek-01")
    sp.set_defaults(func=cmd_account)

    sp = sub.add_parser("deposit", help="入金")
    sp.add_argument("amount", type=float)
    sp.add_argument("--agent", default="leek-01")
    sp.set_defaults(func=cmd_deposit)
