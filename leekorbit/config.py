"""配置加载：platform.yaml 与人设（Persona）。

人设校验是 ADR-0001 的守门员：只允许背景描述，拒绝任何行为剧本。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("LEEK_CONFIG_DIR", "config"))

# 行为指令特征：出现即拒绝加载（ADR-0001：韭菜行为必须涌现，不能注入）
_BEHAVIOR_PATTERNS = [
    r"你会", r"你总是", r"你倾向", r"你喜欢追", r"你习惯",
    r"追涨", r"杀跌", r"割肉", r"梭哈", r"满仓", r"抄底",
    r"听消息", r"频繁交易", r"拿不住", r"恐慌", r"贪婪",
    r"短线思维", r"赌性", r"跟风",
]


class PersonaError(ValueError):
    pass


def validate_persona(persona: dict) -> None:
    for field in ("name", "background", "capital"):
        if field not in persona:
            raise PersonaError(f"人设缺少必填字段: {field}")
    text = yaml.dump(persona, allow_unicode=True)
    for pat in _BEHAVIOR_PATTERNS:
        if re.search(pat, text):
            raise PersonaError(
                f"人设含行为指令特征「{pat}」，违反 ADR-0001（行为必须涌现，不能注入）"
            )


def load_persona(name: str | None = None) -> dict:
    name = name or os.environ.get("LEEK_AGENT", "leek-01")
    path = CONFIG_DIR / "agents" / f"{name}.yaml"
    persona = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_persona(persona)
    return persona


def load_platform() -> dict:
    path = CONFIG_DIR / "platform.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
