"""信息饮食数据层：akshare 封装，带 TTL 缓存、重试与断供降级。

降级原则：拿不到就返回 None，由 feed 层如实写「加载失败」，绝不编造数据。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time

# 东财等国内源走本机代理易被拦截，全部直连
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("TQDM_DISABLE", "1")  # akshare 内部进度条不进日志

log = logging.getLogger(__name__)

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn, retries: int = 3):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    for attempt in range(retries):
        try:
            val = fn()
            _cache[key] = (time.time() + ttl, val)
            return val
        except Exception as e:
            log.warning("datafeed %s attempt %d failed: %s", key, attempt + 1, e)
            time.sleep(0.5 * (attempt + 1))
    if hit:  # 过期缓存好过没有
        log.warning("datafeed %s degraded to stale cache", key)
        return hit[1]
    return None


# -- 大盘 ---------------------------------------------------------------------

def indices() -> list[dict] | None:
    """上证指数/深证成指/创业板指/沪深300 的现价与涨跌幅。"""
    def fetch():
        import akshare as ak

        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        keep = df[df["名称"].isin(["上证指数", "深证成指", "创业板指", "沪深300"])]
        return [
            {"name": r["名称"], "last": float(r["最新价"]), "pct": float(r["涨跌幅"])}
            for _, r in keep.iterrows()
        ]

    return _cached("indices", 60, fetch)


def market_breadth() -> dict | None:
    """涨跌家数（乐咕）。"""
    def fetch():
        import akshare as ak

        df = ak.stock_market_activity_legu()
        m = dict(zip(df["item"], df["value"]))
        return {"up": int(float(m["上涨"])), "down": int(float(m["下跌"])), "limit_up": int(float(m["真实涨停"])), "limit_down": int(float(m["真实跌停"]))}

    return _cached("breadth", 120, fetch)


# -- 个股 ---------------------------------------------------------------------

def quote(symbol: str) -> dict | None:
    """五档快照：最新、昨收、涨停、跌停、今开、最高、最低、换手。"""
    def fetch():
        import akshare as ak

        q = ak.stock_bid_ask_em(symbol=symbol)
        m = dict(zip(q["item"], q["value"]))
        return {
            "symbol": symbol,
            "last": float(m["最新"]),
            "prev_close": float(m["昨收"]),
            "limit_up": float(m["涨停"]),
            "limit_down": float(m["跌停"]),
            "open": float(m["今开"]),
            "high": float(m["最高"]),
            "low": float(m["最低"]),
            "pct": float(m["涨幅"]),
            "turnover": float(m["换手"]),
        }

    return _cached(f"quote:{symbol}", 30, fetch)


def _spot_table():
    """全市场快照表（名称等静态信息用，1小时缓存）。"""
    def fetch():
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        return dict(zip(df["代码"], df["名称"]))

    return _cached("spot_names", 3600, fetch)


def stock_name(symbol: str) -> str | None:
    names = _spot_table()
    return names.get(symbol) if names else None


def daily_kline(symbol: str, days: int = 20) -> list[dict] | None:
    """最近 N 个交易日日K（不复权，散户软件默认视角）。"""
    def fetch():
        import akshare as ak

        start = (dt.date.today() - dt.timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, adjust="")
        df = df.tail(days)
        return [
            {"date": str(r["日期"]), "open": float(r["开盘"]), "close": float(r["收盘"]),
             "high": float(r["最高"]), "low": float(r["最低"]), "pct": float(r["涨跌幅"])}
            for _, r in df.iterrows()
        ]

    return _cached(f"kline:{symbol}:{days}", 3600, fetch)


def stock_news(symbol: str, limit: int = 5) -> list[dict] | None:
    def fetch():
        import akshare as ak

        df = ak.stock_news_em(symbol=symbol).head(limit)
        return [{"time": str(r["发布时间"]), "title": str(r["新闻标题"])} for _, r in df.iterrows()]

    return _cached(f"news:{symbol}", 1800, fetch)


# -- 散户情绪（社媒代理信号） --------------------------------------------------

def hot_rank(top: int = 10) -> list[dict] | None:
    """东财股吧人气榜：散户正在围观什么。"""
    def fetch():
        import akshare as ak

        df = ak.stock_hot_rank_em().head(top)
        return [
            {"rank": int(r["当前排名"]), "symbol": str(r["代码"]).removeprefix("SH").removeprefix("SZ"),
             "name": str(r["股票名称"]), "last": float(r["最新价"]), "pct": float(r["涨跌幅"])}
            for _, r in df.iterrows()
        ]

    return _cached("hot_rank", 300, fetch)


def hot_up(top: int = 5) -> list[dict] | None:
    """人气飙升榜：散户注意力的边际变化。"""
    def fetch():
        import akshare as ak

        df = ak.stock_hot_up_em().head(top)
        return [
            {"symbol": str(r["代码"]).removeprefix("SH").removeprefix("SZ"),
             "name": str(r["股票名称"]), "last": float(r["最新价"]), "pct": float(r["涨跌幅"])}
            for _, r in df.iterrows()
        ]

    return _cached("hot_up", 300, fetch)


def stock_comment(symbol: str) -> dict | None:
    """千股千评：综合得分、机构参与度、关注指数——股吧氛围的量化代理。"""
    def fetch_all():
        import akshare as ak

        return ak.stock_comment_em()

    df = _cached("comment_all", 3600, fetch_all)  # 整表拉一次，各票共享缓存
    if df is None:
        return None
    row = df[df["代码"] == symbol]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {
        "score": float(r["综合得分"]), "rank": int(r["目前排名"]),
        "attention": float(r["关注指数"]), "org_participation": float(r["机构参与度"]),
    }
