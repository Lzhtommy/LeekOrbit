"""LLM 分层路由：cheap（看盘守夜）/ strong（交易决策、复盘）。

统一 OpenAI 兼容接口；未配置 API key 时进入确定性 mock 模式（仅用于开发验证，
mock 数据不计入实验观察，见 ADR-0003）。每次调用的 token 与估算成本入库。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from . import db

log = logging.getLogger(__name__)

DEFAULT_LLM = {
    "base_url": "https://api.deepseek.com",
    "api_key_env": "LEEK_LLM_API_KEY",
    "cheap_model": "deepseek-chat",
    "strong_model": "deepseek-reasoner",
    "pricing_cny_per_mtok": {},
}


class LLM:
    def __init__(self, cfg: dict | None = None, agent: str = "leek-01"):
        self.cfg = {**DEFAULT_LLM, **(cfg or {})}
        self.agent = agent
        key = os.environ.get(self.cfg["api_key_env"]) or os.environ.get("LEEK_LLM_API_KEY")
        base = os.environ.get("LEEK_LLM_BASE_URL", self.cfg["base_url"])
        if key:
            from openai import OpenAI

            self.client = OpenAI(base_url=base, api_key=key)
            self.mock = False
        else:
            self.client = MockClient()
            self.mock = True
            log.warning("LEEK_LLM_API_KEY 未设置，进入 mock 模式（数据不计入实验观察）")

    def model_for(self, tier: str) -> str:
        return self.cfg["strong_model"] if tier == "strong" else self.cfg["cheap_model"]

    def complete(self, tier: str, messages: list[dict], tools: list[dict], wake_id: int | None = None):
        model = self.model_for(tier)
        resp = self.client.chat.completions.create(
            model=model, messages=messages, tools=tools, temperature=1.0
        )
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        price = self.cfg["pricing_cny_per_mtok"].get(model, [0, 0])
        cost = (pt * price[0] + ct * price[1]) / 1_000_000
        db.insert(
            "llm_calls", ts=dt.datetime.now().isoformat(), agent=self.agent, wake_id=wake_id,
            tier=tier, model=model, prompt_tokens=pt, completion_tokens=ct, cost_est=round(cost, 6),
        )
        return resp.choices[0].message


# -- mock 模式 ----------------------------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _msg(content=None, tool_calls=None):
    return _Obj(content=content, tool_calls=tool_calls, role="assistant")


def _tc(call_id, name, args):
    return _Obj(id=call_id, type="function",
                function=_Obj(name=name, arguments=json.dumps(args, ensure_ascii=False)))


class MockClient:
    """确定性 mock 被试：盘中想买热榜第一名 → 触发升级 → 决策层查询后小仓买入；
    晚间写日记；其余场景不动。仅用于系统验证。"""

    def __init__(self):
        self.chat = _Obj(completions=self)

    def create(self, model, messages, tools=None, **kw):
        text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") in ("system", "user"))
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        called = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        resp = self._policy(text, tool_msgs, called, tools or [])
        return _Obj(choices=[_Obj(message=resp)], usage=_Obj(prompt_tokens=0, completion_tokens=0))

    def _policy(self, text, tool_msgs, called, tools):
        import re

        tool_names = {t["function"]["name"] for t in tools}
        hot = re.search(r"1\. (\S+)\((\d{6})\)", text)
        if "晚间" in text and "write_diary" in tool_names and not called:
            note = "今天看了会盘，大盘一般。" + ("买了点股票，希望别套住。" if "持仓" in text else "还没敢下手。")
            return _msg(tool_calls=[_tc("m1", "write_diary", {"content": note})])
        if "盘中" in text and hot and "place_order" in tool_names:
            if not called:
                return _msg(tool_calls=[_tc("m2", "lookup_stock", {"symbol": hot.group(2)})])
            if len(called) == 1:
                return _msg(tool_calls=[_tc("m3", "place_order",
                                             {"side": "buy", "symbol": hot.group(2), "qty": 100})])
        return _msg(content="先不操作，再看看。")
