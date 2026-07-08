import pytest

from leekorbit.llm import LLM, MockClient


class Flaky(MockClient):
    def __init__(self, fail_times):
        super().__init__()
        self.left = fail_times

    def create(self, *a, **kw):
        if self.left > 0:
            self.left -= 1
            raise ConnectionError("api down")
        return super().create(*a, **kw)


def make_llm(fail_times):
    llm = LLM({}, agent="leek-01")
    llm.client = Flaky(fail_times)
    return llm


def test_transient_failure_recovered():
    llm = make_llm(2)
    msg = llm.complete("cheap", [{"role": "user", "content": "hi"}], [])
    assert msg.content


def test_persistent_failure_raises_after_retries():
    llm = make_llm(99)
    with pytest.raises(RuntimeError, match="连续 3 次调用失败"):
        llm.complete("cheap", [{"role": "user", "content": "hi"}], [])
