import pytest

from leekorbit import config
from leekorbit.config import PersonaError, validate_persona

GOOD = {
    "name": "leek-01",
    "background": "32岁，采购专员，攒了10万入市，没有投资经验。",
    "capital": 100000,
}


def test_real_persona_loads_and_validates():
    persona = config.load_persona("leek-01")
    assert persona["capital"] == 100000
    assert persona["routine"]["intraday_interval_minutes"] == 30


def test_missing_field_rejected():
    with pytest.raises(PersonaError, match="缺少必填字段"):
        validate_persona({"name": "x", "background": "y"})


@pytest.mark.parametrize("script", [
    "你会追涨杀跌",
    "你总是听消息炒股",
    "行情好时你倾向于满仓",
    "亏了就割肉",
    "遇到热点你喜欢追进去",
])
def test_behavior_scripts_rejected(script):
    bad = {**GOOD, "background": GOOD["background"] + script}
    with pytest.raises(PersonaError, match="ADR-0001"):
        validate_persona(bad)


def test_pure_background_passes():
    validate_persona(GOOD)
