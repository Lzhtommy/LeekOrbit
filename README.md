# 🌱 LeekOrbit — A股韭菜Agent行为金融学观察实验平台

让一个 LLM Agent 生活在真实 A 股散户的信息环境里（看盘、刷股吧热榜、模拟买卖），
观察追涨杀跌、处置效应等韭菜行为是否**自发涌现**。行为可观测、可复盘是目标；盈亏不是。

- 领域词汇表：[CONTEXT.md](CONTEXT.md)
- 关键决策：[docs/adr/](docs/adr/)（0001 行为涌现不注入 / 0002 账本永不喂回 / 0003 实验参数锁定）
- 实现计划：[docs/PLAN.md](docs/PLAN.md)

## 快速开始

```bash
uv sync

# 配置被试大脑（不配则进入 mock 模式，数据不计入实验观察）——二选一：

# 方案 A：DeepSeek 等 OpenAI 兼容服务（ADR-0003 默认）
export LEEK_LLM_API_KEY=sk-...          # DeepSeek key
# export LEEK_LLM_BASE_URL=...          # 可选，默认 https://api.deepseek.com

# 方案 B：Claude 当被试（官方 anthropic SDK）
# export ANTHROPIC_API_KEY=sk-ant-...
# 并把 config/platform.yaml 的 llm.provider 改为 anthropic、
# cheap_model/strong_model 改为 claude-haiku-4-5 / claude-opus-4-8（文件内有注释模板）
# 注意：正式观察期开始后不许再换（ADR-0003：换模型即换被试）

uv run leekorbit run                    # 常驻：心跳调度 + 仪表盘 http://localhost:8798
```

Docker 常驻（推荐观察期使用）：

```bash
LEEK_LLM_API_KEY=sk-... docker compose up -d --build
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `leekorbit run` | 常驻运行（平台 7×24，Agent 按作息表唤醒） |
| `leekorbit wakes` | 唤醒记录（done / skipped / failed） |
| `leekorbit wake <scene>` | 手动触发一次唤醒 |
| `leekorbit snapshot [id]` | 回放决策快照（它看到了什么、想了什么、做了什么） |
| `leekorbit diary` | 读韭菜日记（它唯一的记忆） |
| `leekorbit feed <scene>` | 预览某场景的信息流 |
| `leekorbit costs` | LLM 成本日报 |
| `leekorbit analyze` | 处置效应统计（只读账本） |
| `leekorbit buy/sell/account/deposit` | 人肉交易所（调试用） |

## 架构一图流

```
心跳调度(scheduler) ──按作息表(routine)──▶ 唤醒(wake)
                                            │  feed.py   信息流：行情+热榜+持仓+日记   ← datafeed(akshare)
                                            │  llm.py    便宜层看盘 / 强层决策(升级机制)
                                            │  tools.py  动作空间：查询(限次)/下单/写日记
                                            ▼
                              exchange.py 模拟交易所(T+1/涨跌停/整手/税费)
                                            ▼
                     SQLite：ledger账本(唯一事实) + snapshots快照 + diary日记 + nav净值
                                            ▼
                              dashboard 观察站(8798) / analysis 行为标注
```

铁律（架构收口，非自觉遵守）：

1. **不写行为剧本**——人设只有背景，校验器拒绝含行为指令的配置（ADR-0001）
2. **账本永不喂回**——Agent 只记得自己写的日记 + 当前持仓资金；完整流水只给研究者（ADR-0002）
3. **观察期内不换模型/人设/频率**——换了就是新被试（ADR-0003）

## 测试

```bash
uv run pytest
```
