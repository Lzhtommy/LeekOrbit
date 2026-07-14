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

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_cache: dict[str, tuple[float, object]] = {}

# -- 东财防封：全局串行限流（东财对单 IP 有风控：每秒>5次/分钟≥200次会临时封禁。
#    社区实践：最小间隔 + 随机抖动；密集重试恰恰是风控最敏感的模式。）
_EM_MIN_INTERVAL = 1.2
_em_lock = __import__("threading").Lock()
_em_last = 0.0


def _em(fn):
    """所有东财系请求（akshare EM 接口 / push2ex）统一经此限流后调用。"""
    import random

    global _em_last
    with _em_lock:
        wait = _em_last + _EM_MIN_INTERVAL + random.uniform(0, 0.5) - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            return fn()
        finally:
            _em_last = time.time()


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


# -- 腾讯行情（主源：不封 IP，字段含权威涨跌停价；索引表实测校准 2026-05） ---------


def _tencent_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"


def _tencent_raw(codes: list[str]) -> dict[str, list[str]]:
    import requests

    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
    r.raise_for_status()
    r.encoding = "gbk"
    out = {}
    for line in r.text.strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].strip().split("_")[-1]
        out[key] = line.split('"')[1].split("~")
    return out


def _parse_tencent_quote(symbol: str, vals: list[str]) -> dict:
    if len(vals) < 53 or not vals[3] or float(vals[3]) == 0:
        raise ValueError(f"tencent 无 {symbol} 行情")
    _sina_names[symbol] = vals[1]  # 名称兜底缓存与新浪共用
    return {
        "symbol": symbol,
        "last": float(vals[3]),
        "prev_close": float(vals[4]),
        "limit_up": float(vals[47]),
        "limit_down": float(vals[48]),
        "open": float(vals[5]),
        "high": float(vals[33]),
        "low": float(vals[34]),
        "pct": float(vals[32]),
        "turnover": float(vals[38] or 0),
    }


def _fetch_quote_tencent(symbol: str) -> dict:
    code = f"{_tencent_prefix(symbol)}{symbol}"
    return _parse_tencent_quote(symbol, _tencent_raw([code])[code])


_TENCENT_INDEX_CODES = {
    "sh000001": "上证指数", "sz399001": "深证成指",
    "sz399006": "创业板指", "sh000300": "沪深300",
}


def _fetch_indices_tencent() -> list[dict]:
    raw = _tencent_raw(list(_TENCENT_INDEX_CODES))
    out = []
    for code, label in _TENCENT_INDEX_CODES.items():
        vals = raw.get(code)
        if vals and len(vals) > 32 and vals[3]:
            out.append({"name": label, "last": float(vals[3]), "pct": float(vals[32])})
    if not out:
        raise ValueError("tencent 指数无数据")
    return out


# -- 新浪备用源（东财 push2 在部分网络下被断连，三源互备） ------------------------

_sina_names: dict[str, str] = {}


def _sina_hq(codes: str) -> list[list[str]]:
    """hq.sinajs.cn 批量行情，返回每个 code 的字段列表。"""
    import requests

    r = requests.get(
        f"https://hq.sinajs.cn/list={codes}",
        headers={"Referer": "https://finance.sina.com.cn"}, timeout=8,
    )
    r.raise_for_status()
    r.encoding = "gbk"
    return [line.split('"')[1].split(",") for line in r.text.strip().splitlines() if '"' in line]


def _sina_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"


def _fetch_quote_sina(symbol: str) -> dict:
    f = _sina_hq(f"{_sina_prefix(symbol)}{symbol}")[0]
    if len(f) < 32 or float(f[3]) == 0:
        raise ValueError(f"sina 无 {symbol} 行情")
    name, prev_close, last = f[0], float(f[2]), float(f[3])
    _sina_names[symbol] = name
    from .exchange import limit_ratio

    ratio = limit_ratio(symbol, name)
    return {
        "symbol": symbol,
        "last": last,
        "prev_close": prev_close,
        "limit_up": round(prev_close * (1 + ratio), 2),
        "limit_down": round(prev_close * (1 - ratio), 2),
        "open": float(f[1]),
        "high": float(f[4]),
        "low": float(f[5]),
        "pct": round((last / prev_close - 1) * 100, 2) if prev_close else 0.0,
        "turnover": 0.0,  # 新浪源无换手率
    }


_SINA_INDEX_CODES = {
    "s_sh000001": "上证指数", "s_sz399001": "深证成指",
    "s_sz399006": "创业板指", "s_sh000300": "沪深300",
}


def _fetch_indices_sina() -> list[dict]:
    rows = _sina_hq(",".join(_SINA_INDEX_CODES))
    return [{"name": f[0], "last": float(f[1]), "pct": float(f[3])} for f in rows]


# -- 大盘 ---------------------------------------------------------------------

def indices() -> list[dict] | None:
    """四大指数现价与涨跌幅。链路：腾讯（不封IP）→ 新浪 → 东财。"""
    def fetch():
        for name, src in (("腾讯", _fetch_indices_tencent), ("新浪", _fetch_indices_sina)):
            try:
                return src()
            except Exception as e:
                log.info("indices %s失败(%s)，切下一源", name, e)

        def em():
            import akshare as ak

            df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
            keep = df[df["名称"].isin(["上证指数", "深证成指", "创业板指", "沪深300"])]
            return [
                {"name": r["名称"], "last": float(r["最新价"]), "pct": float(r["涨跌幅"])}
                for _, r in keep.iterrows()
            ]

        return _em(em)

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
    """实时快照：最新、昨收、涨停、跌停、今开、最高、最低、换手。

    链路：腾讯（不封IP，涨跌停价权威）→ 东财 → 新浪（涨跌停按昨收推算）。
    """
    def fetch():
        try:
            return _fetch_quote_tencent(symbol)
        except Exception as e:
            log.info("quote %s 腾讯失败(%s)，切东财", symbol, e)

        def em():
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

        try:
            return _em(em)
        except Exception as e:
            log.info("quote %s 东财失败(%s)，切新浪", symbol, e)
            return _fetch_quote_sina(symbol)

    return _cached(f"quote:{symbol}", 30, fetch)


def _spot_table():
    """全市场快照表（名称等静态信息用，1小时缓存）。"""
    def fetch():
        def em():
            import akshare as ak

            df = ak.stock_zh_a_spot_em()
            return dict(zip(df["代码"], df["名称"]))

        return _em(em)

    return _cached("spot_names", 3600, fetch)


def stock_name(symbol: str) -> str | None:
    names = _spot_table()
    if names and names.get(symbol):
        return names[symbol]
    if symbol not in _sina_names:
        quote(symbol)  # 兜底：新浪行情顺带取名称
    return _sina_names.get(symbol)


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

    return _cached(f"kline:{symbol}:{days}", 3600, lambda: _em(fetch))


def stock_news(symbol: str, limit: int = 5) -> list[dict] | None:
    def fetch():
        import akshare as ak

        df = ak.stock_news_em(symbol=symbol).head(limit)
        return [{"time": str(r["发布时间"]), "title": str(r["新闻标题"])} for _, r in df.iterrows()]

    return _cached(f"news:{symbol}", 1800, lambda: _em(fetch))


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

    return _cached("hot_rank", 300, lambda: _em(fetch))


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

    return _cached("hot_up", 300, lambda: _em(fetch))


def stock_comment(symbol: str) -> dict | None:
    """千股千评：综合得分、机构参与度、关注指数——股吧氛围的量化代理。"""
    def fetch_all():
        import akshare as ak

        return ak.stock_comment_em()

    df = _cached("comment_all", 3600, lambda: _em(fetch_all))  # 整表拉一次，各票共享缓存
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


def display_name(symbol: str, ledger_name: str | None) -> str:
    """展示用名称：账本里是空/代码占位时实时补查，查不到再退回代码。"""
    if ledger_name and ledger_name != symbol:
        return ledger_name
    return stock_name(symbol) or symbol


# -- 打板/题材（散户题材叙事的主粮；东财 push2ex，走 _em 限流） -------------------

def _zt_raw(date: str) -> list[dict]:
    import requests

    def em():
        r = requests.get(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                    "Pageindex": 0, "pagesize": 200, "sort": "fbt:asc", "date": date},
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        r.raise_for_status()
        return (r.json().get("data") or {}).get("pool") or []

    return _em(em)


def zt_pool() -> list[dict] | None:
    """今日涨停池：名称、行业、连板数、N天M板、炸板次数。按连板数降序。"""
    def fetch():
        pool = _zt_raw(dt.date.today().strftime("%Y%m%d"))
        out = [{
            "symbol": str(p["c"]).zfill(6), "name": p["n"],
            "industry": p.get("hybk", ""), "limit_days": p.get("lbc", 1),
            "break_times": p.get("zbc", 0),
            "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板',
        } for p in pool]
        return sorted(out, key=lambda x: -x["limit_days"])

    return _cached("zt_pool", 300, fetch)


# -- 财联社快讯（v1 API + 本地签名，零 key；与东财不同源不同风控面） ----------------

def cls_news(limit: int = 8) -> list[dict] | None:
    """财联社电报：全市场财经快讯。"""
    def fetch():
        import hashlib

        import requests

        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
                  "last_time": "", "refresh_type": "1", "rn": str(max(limit, 20))}
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
        r = requests.get(f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}",
                         headers={"User-Agent": UA, "Referer": "https://www.cls.cn/"}, timeout=10)
        r.raise_for_status()
        rows = []
        for item in (r.json().get("data") or {}).get("roll_data", []) or []:
            ts = item.get("ctime")
            rows.append({
                "time": dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "",
                "title": item.get("title") or (item.get("brief") or "")[:80],
            })
        if not rows:
            raise ValueError("cls 空返回")
        return rows[:limit]

    return _cached("cls_news", 600, fetch)
