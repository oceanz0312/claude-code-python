# claude-code-python

[![Python >= 3.9](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)]()

> **Claude Code CLI 的 Python SDK** — [claude-code-node](https://github.com/oceanz0312/claude-code-node) 的完整 Python 复刻版本，让你在 Python 中以编程方式驱动 Claude Code，干净的异步 API。

[English Documentation](./README.md)

---

## 你能得到什么

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

# 缓冲模式 — 收集所有事件，获取最终响应
turn = await session.run("Fix the failing tests in src/")
print(turn.final_response)

# 多轮对话：直接继续 run() — 会话恢复是自动的
turn2 = await session.run("Now add test coverage for edge cases")
```

**没有 HTTP 服务器，没有协议转换，没有过度抽象。** 只是对 Claude Code CLI 的类型化异步封装，替你处理繁琐的部分：

| 能力 | 做了什么 |
|------|----------|
| **会话管理** | 跨轮次自动 `--resume` — 你永远不需要手动管理 session ID |
| **流式输出** | `AsyncIterator[RelayEvent]`，7 种类型化事件 |
| **35+ CLI 选项** | 每个常用 flag 都有类型化字段 — `model`、`system_prompt`、`allowed_tools`、`json_schema`、`max_budget_usd`、`agents`、`mcp_config`… |
| **结构化输出** | 传入 JSON Schema，从 `turn.structured_output` 拿到解析后的对象 |
| **图片输入** | 本地截图和文本一起发送 |
| **中断控制** | 用 `asyncio.Event` 或任意 signal 对象取消任意 turn |
| **Fail-fast** | 秒级检测 API 错误，而非等待数分钟（CI/CD 场景关键能力） |
| **零依赖** | 纯 Python 标准库实现 — 没有第三方依赖 |

---

## 纯 TDD 驱动 — 完美复原 Claude Code CLI

本项目是 [claude-code-node](https://github.com/oceanz0312/claude-code-node)（TypeScript）到 Python 的 **1:1 忠实复刻**，全程采用 **测试驱动开发（TDD）** 实现：

> **先写测试，再写代码。** 每个模块的实现方式都是：先移植 TypeScript 的测试用例，再编写生产代码直到全部测试通过。最终得到的不是"尽力而为的翻译"，而是一份**经过验证的、行为完全一致的复刻**。

TDD 流程保证了：

- **35+ CLI 参数** — 完全一致的覆盖范围，每个 flag 都有测试验证
- **双层流式去重** — 相同算法，相同行为，相同边界情况
- **7 种中继事件** — 相同的语义事件模型，相同的序列化方式
- **Fail-fast 机制** — 相同的 stderr + stdout 检测，相同的时序
- **原始事件日志** — 相同的 NDJSON 格式，相同的文件布局
- **环境隔离** — 相同的子进程沙箱，相同的环境变量继承规则
- **全部 70+ 测试通过** — 单元测试 + 真实模型 E2E 测试，**全部绿灯，零跳过**

每个测试用例都与 TypeScript 版本 1:1 对应。TypeScript SDK 有的测试，Python SDK 同样有 — 并且全部通过。

---

## 经过测试，值得信赖

| 指标 | 详情 |
|------|------|
| **70+ 个测试用例** | 单元测试 + 真实模型 E2E 测试 — **全部通过** |
| **Fake CLI 模拟器** | `fake_claude.py` 完整模拟 `stream-json` 协议 — 单元测试无需真实 CLI 或 API Key |
| **真实 E2E 测试套件** | 使用真实凭据调用 Claude CLI — 测试多轮记忆、认证路径、系统提示词、图片理解、Agent 身份识别、15+ CLI 参数转发 |
| **E2E 测试产物** | 每次运行保存 NDJSON 日志、中继事件和最终响应，便于事后分析 |
| **零依赖** | 纯标准库 — 无需审计，不会崩 |

---

## 流式输出示例

```python
import asyncio
from claude_code import ClaudeCode, SessionOptions

async def main():
    claude = ClaudeCode()
    session = claude.start_session(SessionOptions(
        model="sonnet",
        dangerously_skip_permissions=True,
    ))

    streamed = await session.run_streamed("用 Python 解释异步编程")

    async for event in streamed.events:
        if event.type == "text_delta":
            print(event.content, end="", flush=True)
        elif event.type == "turn_complete":
            print(f"\n\nTokens: {event.input_tokens} 输入 / {event.output_tokens} 输出")
            print(f"费用: ${event.cost_usd:.4f}")

asyncio.run(main())
```

---

## 结构化输出

```python
from claude_code import ClaudeCode, SessionOptions

schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name", "age"],
}

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    json_schema=schema,
    dangerously_skip_permissions=True,
))

turn = await session.run("提取信息：张三今年 30 岁")
print(turn.structured_output)  # {"name": "张三", "age": 30}
```

---

## 图片输入

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

turn = await session.run([
    {"type": "text", "text": "这张图片里有什么？"},
    {"type": "local_image", "path": "./screenshot.png"},
])
print(turn.final_response)
```

---

## 会话管理

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()

# 开始新会话
session = claude.start_session(SessionOptions(model="sonnet"))
turn1 = await session.run("记住：暗号是 42")

# 跨轮次自动恢复
turn2 = await session.run("暗号是什么？")  # "42"

# 或者通过 session ID 在新实例中恢复
session2 = claude.resume_session(session.id, SessionOptions(model="sonnet"))
turn3 = await session2.run("暗号是什么？")  # 仍然是 "42"

# 或者继续最近的会话
session3 = claude.continue_session(SessionOptions(model="sonnet"))
```

---

## 中断 / 取消

```python
import asyncio
from claude_code import ClaudeCode, SessionOptions, TurnOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

abort = asyncio.Event()

# 5 秒后取消
asyncio.get_event_loop().call_later(5, abort.set)

turn = await session.run(
    "写一篇关于计算机发展史的长篇论文",
    TurnOptions(signal=abort),
)
```

---

## 认证方式

```python
from claude_code import ClaudeCode, ClaudeCodeOptions

# 方式 1：API Key
claude = ClaudeCode(ClaudeCodeOptions(api_key="sk-ant-..."))

# 方式 2：Auth Token + Base URL（用于代理 / 兼容端点）
claude = ClaudeCode(ClaudeCodeOptions(
    auth_token="your-token",
    base_url="https://your-proxy.com/v1",
))

# 方式 3：环境变量（ANTHROPIC_API_KEY、ANTHROPIC_AUTH_TOKEN、ANTHROPIC_BASE_URL）
claude = ClaudeCode()
```

---

## 全部 SessionOptions

```python
@dataclass
class SessionOptions:
    # 模型与上下文
    model: str                          # 模型名（如 "sonnet"、"opus"）
    cwd: str                            # CLI 工作目录
    additional_directories: list[str]   # 额外暴露的目录
    max_turns: int                      # 最大 Agent 轮次
    max_budget_usd: float               # 费用上限

    # 系统提示词
    system_prompt: str                  # 覆盖系统提示词
    system_prompt_file: str             # 从文件加载系统提示词
    append_system_prompt: str           # 追加到默认系统提示词
    append_system_prompt_file: str      # 从文件追加

    # 权限
    permission_mode: PermissionMode     # "default" | "acceptEdits" | "plan" | "auto" | "dontAsk" | "bypassPermissions"
    dangerously_skip_permissions: bool  # 跳过所有权限提示
    allowed_tools: list[str]            # 工具白名单
    disallowed_tools: list[str]         # 工具黑名单

    # MCP
    mcp_config: str | list[str]        # MCP 配置文件路径

    # Agents
    agents: dict | str                  # 子 Agent 定义

    # 结构化输出
    json_schema: str | dict             # JSON Schema

    # ... 以及 20+ 更多选项
```

---

## 架构

```
┌──────────────┐     ┌────────────┐     ┌──────────────┐
│  你的代码     │────▶│  Session    │────▶│ ClaudeCodeExec│
│              │     │ (状态管理)   │     │ (子进程管理)   │
│  ClaudeCode  │     │ (流式去重)   │     │ (stdin/stdout)│
│  (入口)       │     │ (Fail-fast) │     │ (中断控制)     │
└──────────────┘     └──────┬─────┘     └───────┬───────┘
                            │                   │
                     ┌──────▼─────┐      ┌──────▼───────┐
                     │ Translator  │      │ Claude Code  │
                     │ (原始→中继)  │      │ CLI 进程      │
                     └─────────────┘      └──────────────┘
```

**数据流**：你的代码 → `ClaudeCode` → `Session` → `ClaudeCodeExec` → 启动 `claude` CLI 子进程 → stdout NDJSON → `parse_line()` → `Translator.translate()` → `RelayEvent` 流 → 你的回调或异步迭代器。

---

## 中继事件类型

| 事件 | 字段 | 说明 |
|------|------|------|
| `TextDeltaEvent` | `content` | 增量文本输出 |
| `ThinkingDeltaEvent` | `content` | 增量思考/推理 |
| `ToolUseEvent` | `tool_use_id`, `tool_name`, `input` | 工具调用 |
| `ToolResultEvent` | `tool_use_id`, `output`, `is_error` | 工具执行结果 |
| `SessionMetaEvent` | `model` | 会话元数据（模型名） |
| `TurnCompleteEvent` | `session_id`, `cost_usd`, `input_tokens`, `output_tokens`, `context_window` | 轮次完成 |
| `ErrorEvent` | `message`, `session_id` | 发生错误 |

---

## 它不是什么

- 不是 HTTP API 服务
- 不是多模型网关（它封装的是 Claude Code，仅此而已）
- 不是 CLI 的替代品（它驱动 CLI）

---

## 前置要求

- Python >= 3.9
- 已安装 Claude Code CLI，且 `claude` 可在 `PATH` 中找到（或通过 `cli_path` 显式指定路径）

## 安装

```bash
# 直接从 GitHub 安装
pip install git+https://github.com/oceanz0312/claude-code-python.git

# 或克隆后以开发模式安装
git clone https://github.com/oceanz0312/claude-code-python.git
cd claude-code-python
pip install -e .
```

---

## E2E 测试

仓库内置了针对 Claude Code CLI 的真实端到端测试：

```bash
# 1. 设置认证环境变量
export E2E_MODEL=sonnet
export E2E_API_KEY=sk-ant-...        # 或使用 auth token：
# export E2E_AUTH_TOKEN=your-token
# export E2E_BASE_URL=https://your-proxy.com/

# 2. 运行 E2E 测试套件
python -m pytest tests/e2e/ -v
```

---

## 开发

```bash
# 以开发模式安装
pip install -e .

# 运行全部测试
python -m pytest tests/ -v

# 仅运行单元测试
python -m pytest tests/unit/ -v

# 仅运行 E2E 测试
python -m pytest tests/e2e/ -v
```

---

## 相关项目

| 项目 | 说明 |
|------|------|
| [claude-code-node](https://github.com/oceanz0312/claude-code-node) | 原版 TypeScript SDK — 本项目是其 Python 复刻 |
| [claude-code-openai-wrapper](https://github.com/RichardAtCT/claude-code-openai-wrapper) | Python/FastAPI 服务，将 Claude Code 暴露为 OpenAI 兼容 API |
| [claude-code-api](https://github.com/bethington/claude-code-api) | Node.js/Express 桥接到 OpenAI 兼容端点 |
| [claude-code-api-rs](https://github.com/ZhangHanDong/claude-code-api-rs) | Rust/Axum 高性能 API 服务 |

---

## 许可证

[MIT](LICENSE)
