import datetime as dt

import pytest

from leekorbit import exchange
from leekorbit.exchange import Fill, OrderRejected, Quote

A = "leek-01"
D1 = dt.datetime(2026, 7, 8, 10, 0)
D2 = dt.datetime(2026, 7, 9, 10, 0)


def q(symbol="600000", name="浦发银行", last=10.0, prev_close=10.0, **kw):
    return Quote(symbol=symbol, name=name, last=last, prev_close=prev_close, **kw)


@pytest.fixture(autouse=True)
def seed():
    exchange.deposit(A, 100000, D1 - dt.timedelta(days=1), note="初始本金")


def buy(qty=100, quote=None, **kw):
    return exchange.place_order(A, "buy", qty, quote or q(), now=D1, **kw)


# -- 规则边界 -----------------------------------------------------------------

def test_t_plus_1_same_day_sell_rejected():
    buy(1000)
    with pytest.raises(OrderRejected, match="T\\+1"):
        exchange.place_order(A, "sell", 1000, q(), now=D1)


def test_t_plus_1_next_day_sell_ok():
    buy(1000)
    fill = exchange.place_order(A, "sell", 1000, q(), now=D2)
    assert isinstance(fill, Fill)


def test_limit_up_buy_rejected():
    quote = q(last=11.0, prev_close=10.0)  # 主板 10% 涨停封板
    with pytest.raises(OrderRejected, match="涨停"):
        buy(100, quote)


def test_limit_down_sell_rejected():
    buy(1000)
    quote = q(last=9.0, prev_close=10.0)
    with pytest.raises(OrderRejected, match="跌停"):
        exchange.place_order(A, "sell", 1000, quote, now=D2)


def test_st_5_percent_band():
    quote = q(symbol="600123", name="ST某某", last=10.5, prev_close=10.0)
    with pytest.raises(OrderRejected, match="涨停"):
        buy(100, quote)


def test_odd_lot_buy_rejected():
    with pytest.raises(OrderRejected, match="100股整数倍"):
        buy(150)


def test_odd_lot_sell_allowed_only_as_full_position():
    buy(1000)
    with pytest.raises(OrderRejected, match="100股整数倍"):
        exchange.place_order(A, "sell", 950, q(), now=D2)


def test_insufficient_cash_rejected():
    with pytest.raises(OrderRejected, match="可用资金不足"):
        buy(100, q(last=2000.0, prev_close=1900.0))


def test_chinext_without_permission_rejected():
    with pytest.raises(OrderRejected, match="创业板"):
        buy(100, q(symbol="300750", name="宁德时代"))


def test_star_and_bse_rejected():
    for sym in ("688111", "830799"):
        with pytest.raises(OrderRejected):
            buy(100, q(symbol=sym, name="某某"))


def test_chinext_with_permission_and_20cm_band():
    fill = exchange.place_order(
        A, "buy", 100, q(symbol="300750", name="宁德时代", last=110.0, prev_close=100.0),
        now=D1, permissions=("main_board", "chinext"),
    )
    assert fill.price == 110.0  # 20% 板内可成交
    with pytest.raises(OrderRejected, match="涨停"):
        exchange.place_order(
            A, "buy", 100, q(symbol="300750", name="宁德时代", last=120.0, prev_close=100.0),
            now=D1, permissions=("main_board", "chinext"),
        )


def test_limit_order_out_of_band_rejected():
    with pytest.raises(OrderRejected, match="涨跌停价范围"):
        buy(100, q(), limit_price=11.5)


def test_limit_order_not_marketable_is_dead():
    with pytest.raises(OrderRejected, match="废单"):
        buy(100, q(last=10.0), limit_price=9.8)


def test_limit_order_marketable_fills_at_snapshot():
    fill = buy(100, q(last=10.0), limit_price=10.2)
    assert fill.price == 10.0


# -- 费用与账本 ---------------------------------------------------------------

def test_fees_exact_roundtrip():
    fill = buy(1000, q(last=10.0))  # 金额 10000
    assert fill.commission == 5.0   # 万2.5 不足5元取5元
    assert fill.stamp_tax == 0.0    # 买入无印花税
    assert fill.transfer_fee == 0.1
    assert fill.cash_delta == -10005.10
    assert exchange.cash(A) == 100000 - 10005.10

    sell = exchange.place_order(A, "sell", 1000, q(last=12.0), now=D2)  # 金额 12000
    assert sell.commission == 5.0
    assert sell.stamp_tax == 6.0    # 卖出 0.05%
    assert sell.transfer_fee == 0.12
    assert sell.cash_delta == 12000 - 5.0 - 6.0 - 0.12
    assert exchange.cash(A) == round(100000 - 10005.10 + 11988.88, 2)


def test_commission_above_minimum():
    fill = buy(9000, q(last=10.0))  # 金额 90000 → 佣金 22.5
    assert fill.commission == 22.5


def test_positions_derived_from_ledger_only():
    buy(1000, q(last=10.0))
    exchange.place_order(A, "sell", 500, q(last=11.0), now=D2)
    pos = exchange.positions(A)
    assert pos["600000"]["qty"] == 500
    assert exchange.sellable(A, "600000", D2.date()) == 500
    # 全部卖出后持仓消失
    exchange.place_order(A, "sell", 500, q(last=11.0), now=D2 + dt.timedelta(hours=1))
    assert "600000" not in exchange.positions(A)


def test_sellable_excludes_today_buys():
    buy(1000, q(last=10.0))                                   # D1 买
    exchange.place_order(A, "buy", 500, q(last=10.0), now=D2)  # D2 再买
    assert exchange.sellable(A, "600000", D2.date()) == 1000   # 只可卖 D1 部分
