import json

import pytest

from leekorbit.llm import LLM, AnthropicAdapter, _Obj


class FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeAnthropicClient:
    """记录请求参数并返回一个含 text + tool_use 的固定响应。"""

    def __init__(self):
        self.last_params = None
        self.messages = self

    def create(self, **params):
        self.last_params = params
        return _Obj(
            content=[
                FakeBlock(type="text", text="我看看工商银行"),
                FakeBlock(type="tool_use", id="toolu_01", name="lookup_stock",
                          input={"symbol": "601398"}),
            ],
            usage=_Obj(input_tokens=1200, output_tokens=80),
        )


@pytest.fixture
def adapter():
    a = AnthropicAdapter.__new__(AnthropicAdapter)  # 跳过 __init__，不需要真实凭证
    a._client = FakeAnthropicClient()
    a.chat = _Obj(completions=a)
    return a


OPENAI_MESSAGES = [
    {"role": "system", "content": "你是老李。"},
    {"role": "user", "content": "【大盘】..."},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "toolu_00", "type": "function",
                     "function": {"name": "lookup_stock", "arguments": '{"symbol": "000977"}'}}]},
    {"role": "tool", "tool_call_id": "toolu_00", "content": "浪潮信息 涨停"},
]

TOOLS = [{"type": "function", "function": {
    "name": "lookup_stock", "description": "查股票",
    "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                   "required": ["symbol"]}}}]


def test_request_conversion(adapter):
    adapter.create(model="claude-opus-4-8", messages=OPENAI_MESSAGES, tools=TOOLS,
                   temperature=1.0)  # 采样参数必须被丢弃
    p = adapter._client.last_params
    assert "temperature" not in p and "top_p" not in p
    assert p["system"] == "你是老李。"
    assert p["messages"][0] == {"role": "user", "content": "【大盘】..."}
    tool_use = p["messages"][1]["content"][0]
    assert tool_use["type"] == "tool_use" and tool_use["input"] == {"symbol": "000977"}
    result = p["messages"][2]["content"][0]
    assert result == {"type": "tool_result", "tool_use_id": "toolu_00", "content": "浪潮信息 涨停"}
    assert p["tools"][0] == {"name": "lookup_stock", "description": "查股票",
                             "input_schema": TOOLS[0]["function"]["parameters"]}


def test_response_conversion(adapter):
    resp = adapter.create(model="claude-opus-4-8", messages=OPENAI_MESSAGES)
    msg = resp.choices[0].message
    assert msg.content == "我看看工商银行"
    tc = msg.tool_calls[0]
    assert tc.id == "toolu_01"
    assert tc.function.name == "lookup_stock"
    assert json.loads(tc.function.arguments) == {"symbol": "601398"}
    assert resp.usage.prompt_tokens == 1200 and resp.usage.completion_tokens == 80


def test_consecutive_tool_results_merge(adapter):
    msgs = OPENAI_MESSAGES + [{"role": "tool", "tool_call_id": "toolu_00b", "content": "第二个结果"}]
    adapter.create(model="claude-opus-4-8", messages=msgs)
    user_msg = adapter._client.last_params["messages"][2]
    assert [b["tool_use_id"] for b in user_msg["content"]] == ["toolu_00", "toolu_00b"]


def test_provider_selection_mock_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LEEK_LLM_API_KEY", raising=False)
    llm = LLM({"provider": "anthropic"})
    assert llm.mock is True


def test_provider_selection_anthropic_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    llm = LLM({"provider": "anthropic"})
    assert llm.mock is False
    assert isinstance(llm.client, AnthropicAdapter)


def test_empty_env_strings_do_not_shadow_config(monkeypatch):
    """compose 的 ${VAR:-} 默认值会注入空字符串，必须视为未设置。"""
    monkeypatch.setenv("LEEK_LLM_PROVIDER", "")
    monkeypatch.setenv("LEEK_LLM_API_KEY", "")
    monkeypatch.setenv("LEEK_LLM_BASE_URL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    llm = LLM({"provider": "anthropic"})
    assert llm.mock is False and isinstance(llm.client, AnthropicAdapter)
