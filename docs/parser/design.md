# claude-code-parser Python 移植技术方案

> 对标源码：`claude-code-parser@0.1.1`（npm 包，MIT 许可）  
> 目标：在 `claude-code-python` 仓库中作为独立子包 `claude_code.parser` 实现完整功能对等移植

---

## 1. 源码定位与模块总览

原始 npm 包共含 5 个逻辑模块：

| 原始模块 | 职责 | Python 对应文件 |
|---------|------|----------------|
| `types/protocol.ts` | CLI 线路格式类型（ClaudeEvent, ClaudeMessage, ClaudeContent, ModelUsageEntry） | `parser/protocol.py` |
| `types/events.ts` | 翻译后 RelayEvent 联合类型（7 种事件） | `parser/events.py` |
| `parser.ts` | `parseLine()` — JSON 行解析 | `parser/parse.py` |
| `translator.ts` | `Translator` 类 + `extractContent()` + 内部辅助函数 | `parser/translator.py` |
| `writer.ts` | `createMessage` — 构造 stdin NDJSON 消息 | `parser/writer.py` |

依赖关系：
```
parser/__init__.py          # 公开 API 导出
  ├── protocol.py           # 纯类型，零依赖
  ├── events.py             # 纯类型，零依赖
  ├── parse.py              # 依赖 protocol
  ├── translator.py         # 依赖 protocol + events
  └── writer.py             # 独立，零依赖
```

---

## 2. 目录结构

```
claude_code/
  parser/
    __init__.py              # 公开导出
    protocol.py              # 线路格式 dataclass
    events.py                # RelayEvent 联合类型
    parse.py                 # parseLine()
    translator.py            # Translator 类 + extractContent + blockFingerprint + parseDoubleEncodedResult
    writer.py                # createMessage 工具
tests/
  parser/
    __init__.py
    test_parse.py            # parseLine 单元测试
    test_translator.py       # Translator 全量测试（去重、上下文切换、错误、usage 聚合）
    test_writer.py           # createMessage 测试
    test_extract_content.py  # extractContent 边界测试
    conftest.py              # 共享 fixture
```

---

## 3. 类型定义

### 3.1 protocol.py — 线路格式类型

严格对应 `claude-code-parser/dist/types/protocol.d.ts`，使用 `@dataclass` + `Optional` 字段。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class ModelUsageEntry:
    """对应 claude-code-parser ModelUsageEntry 接口。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    context_window: int = 0

@dataclass
class ClaudeContent:
    """
    多态 content block。
    对应原始 ClaudeContent 接口——type 字段为 string（非枚举），
    因为 Claude Code CLI 可能随时新增 block 类型。
    """
    type: str
    text: Optional[str] = None
    thinking: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Any = None
    content: Any = None
    tool_use_id: Optional[str] = None
    is_error: Optional[bool] = None

@dataclass
class ClaudeMessage:
    """对应 claude-code-parser ClaudeMessage 接口。"""
    content: list[ClaudeContent] = field(default_factory=list)
    role: Optional[str] = None
    stop_reason: Optional[str] = None
    id: Optional[str] = None

@dataclass
class ClaudeEvent:
    """
    原始 NDJSON 事件信封。
    对应 claude-code-parser ClaudeEvent 接口。
    字段使用 snake_case 以匹配 JSON 线路格式。
    """
    type: str
    subtype: Optional[str] = None
    message: Optional[ClaudeMessage] = None
    result: Any = None               # 可能是 double-encoded JSON 字符串
    session_id: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list[str]] = None
    duration_ms: Optional[int] = None
    duration_api_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    total_cost_usd: Optional[float] = None
    is_error: Optional[bool] = None
    num_turns: Optional[int] = None
    model_usage: Optional[dict[str, ModelUsageEntry]] = None  # 原始字段名 modelUsage
    usage: Any = None
    event: Any = None                # stream_event envelope payload
    attempt: Optional[int] = None    # system.api_retry 扩展字段
    max_retries: Optional[int] = None
    retry_delay_ms: Optional[int] = None
    error_status: Optional[int] = None
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)
```

除了 `claude-code-parser` `d.ts` 中显式声明的字段外，Python 版还必须额外保留
`agent-sdk/src/session.ts` 通过松散对象读取的扩展字段：

- `ClaudeMessage.id`：供 session 层 `getMessageId()` / 双层去重使用
- `ClaudeEvent.event`：承载 `type == "stream_event"` 的 envelope payload
- `attempt` / `max_retries` / `retry_delay_ms` / `error_status` / `error`：承载
  `system.api_retry` fail-fast 所需信息
- `raw`：保留原始 JSON dict，便于未来 CLI 协议新增字段时前向兼容

#### 反序列化策略

原始 JS 代码直接 `JSON.parse()` 得到松散 object。Python 需要从 `dict` 构建 dataclass：

```python
def claude_event_from_dict(data: dict[str, Any]) -> ClaudeEvent:
    """从 JSON dict 构建 ClaudeEvent，处理嵌套 message 和 modelUsage。"""
    message = None
    if "message" in data and data["message"] is not None:
        msg_data = data["message"]
        content_list = []
        for c in msg_data.get("content", []):
            if isinstance(c, dict):
                content_list.append(ClaudeContent(**{
                    k: v for k, v in c.items()
                    if k in ClaudeContent.__dataclass_fields__
                }))
        message = ClaudeMessage(
            content=content_list,
            role=msg_data.get("role"),
            stop_reason=msg_data.get("stop_reason"),
            id=msg_data.get("id"),
        )

    model_usage = None
    raw_usage = data.get("modelUsage")
    if isinstance(raw_usage, dict):
        model_usage = {}
        for model_id, entry in raw_usage.items():
            if isinstance(entry, dict):
                model_usage[model_id] = ModelUsageEntry(
                    input_tokens=entry.get("inputTokens", 0),
                    output_tokens=entry.get("outputTokens", 0),
                    cache_read_input_tokens=entry.get("cacheReadInputTokens", 0),
                    cache_creation_input_tokens=entry.get("cacheCreationInputTokens", 0),
                    context_window=entry.get("contextWindow", 0),
                )

    return ClaudeEvent(
        type=data.get("type", ""),
        subtype=data.get("subtype"),
        message=message,
        result=data.get("result"),
        session_id=data.get("session_id"),
        model=data.get("model"),
        tools=data.get("tools"),
        duration_ms=data.get("duration_ms"),
        duration_api_ms=data.get("duration_api_ms"),
        cost_usd=data.get("cost_usd"),
        total_cost_usd=data.get("total_cost_usd"),
        is_error=data.get("is_error"),
        num_turns=data.get("num_turns"),
        model_usage=model_usage,
        usage=data.get("usage"),
        event=data.get("event"),
        attempt=data.get("attempt"),
        max_retries=data.get("max_retries"),
        retry_delay_ms=data.get("retry_delay_ms"),
        error_status=data.get("error_status"),
        error=data.get("error"),
        raw=dict(data),
    )
```

### 3.2 events.py — 翻译后事件类型

严格对应 `claude-code-parser/dist/types/events.d.ts`，7 种事件形成 Union 类型：

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Union

@dataclass
class TextDeltaEvent:
    type: str = "text_delta"    # 字面量标记
    content: str = ""

@dataclass
class ThinkingDeltaEvent:
    type: str = "thinking_delta"
    content: str = ""

@dataclass
class ToolUseEvent:
    type: str = "tool_use"
    tool_use_id: str = ""
    tool_name: str = ""
    input: str = ""             # JSON 字符串

@dataclass
class ToolResultEvent:
    type: str = "tool_result"
    tool_use_id: str = ""
    output: str = ""
    is_error: bool = False

@dataclass
class SessionMetaEvent:
    type: str = "session_meta"
    model: str = "unknown"

@dataclass
class TurnCompleteEvent:
    type: str = "turn_complete"
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    context_window: Optional[int] = None

@dataclass
class ErrorEvent:
    type: str = "error"
    message: str = ""
    session_id: Optional[str] = None

# 联合类型
RelayEvent = Union[
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolUseEvent,
    ToolResultEvent,
    SessionMetaEvent,
    TurnCompleteEvent,
    ErrorEvent,
]
```

---

## 4. parseLine — 行解析器

### 4.1 原始逻辑（对标 parser.js）

```javascript
export function parseLine(line) {
    const trimmed = line.trim();
    if (trimmed.length === 0) return null;
    try { return JSON.parse(trimmed); }
    catch { return null; }
}
```

### 4.2 Python 实现

```python
import json
from .protocol import ClaudeEvent, claude_event_from_dict

def parse_line(line: str) -> ClaudeEvent | None:
    """
    解析一行 NDJSON 为 ClaudeEvent。
    对应 claude-code-parser parseLine()。

    - 空行返回 None
    - JSON 解析失败返回 None（静默跳过）
    """
    trimmed = line.strip()
    if not trimmed:
        return None
    try:
        data = json.loads(trimmed)
        if not isinstance(data, dict):
            return None
        return claude_event_from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
```

---

## 5. Translator — 核心翻译器

### 5.1 整体架构

`Translator` 是有状态的事件翻译器，将原始 `ClaudeEvent` 翻译为 `RelayEvent[]`。核心职责：

1. **事件路由**：根据 `type` 字段分发到 `_translate_system` / `_translate_result` / `_translate_assistant` / `_translate_user`
2. **去重**：assistant 事件携带累积快照，通过 `lastContentIndex` + `blockFingerprint` 实现增量提取
3. **元数据捕获**：从 `system.init` 捕获 session_id 和 model
4. **双重编码处理**：result 字段可能是 double-encoded JSON

### 5.2 去重算法详解（对标 translateAssistant）

原始 JS 逻辑：

```javascript
translateAssistant(raw) {
    const msg = raw.message;
    if (!msg?.content || msg.content.length === 0) return [];

    // 指纹检测上下文切换
    const firstKey = blockFingerprint(msg.content[0]);
    if (firstKey !== this.lastFirstBlockKey) {
        this.lastContentIndex = 0;
        this.lastFirstBlockKey = firstKey;
    }

    // 安全回退：内容缩短时重置
    if (msg.content.length < this.lastContentIndex) {
        this.lastContentIndex = 0;
    }

    const events = [];
    // 仅处理尚未发送的 block（从 lastContentIndex 开始）
    for (let i = this.lastContentIndex; i < msg.content.length; i++) {
        const block = msg.content[i];
        const ev = this.translateContentBlock(block);
        if (ev) events.push(ev);
    }

    this.lastContentIndex = msg.content.length;
    return events;
}
```

核心洞察：
- Claude Code 的 `assistant` 事件每次携带**全量**内容块快照
- `lastContentIndex` 记录上次处理到第几个块，只发送新增部分
- `blockFingerprint` 检测第一个块是否变化（子代理交错场景），变化时重置索引

### 5.3 blockFingerprint 实现

原始逻辑：

```javascript
function blockFingerprint(block) {
    if (block.id) return `${block.type}:${block.id}`;
    const text = block.thinking ?? block.text ?? '';
    if (text) return `${block.type}:${text.slice(0, 64)}`;
    return `${block.type}:${block.tool_use_id ?? 'unknown'}`;
}
```

Python 实现：

```python
def _block_fingerprint(block: ClaudeContent) -> str:
    """
    生成内容块指纹，用于检测上下文切换。
    对标 claude-code-parser blockFingerprint()。

    优先级：
    1. block.id (tool_use 块有唯一 ID)
    2. thinking/text 的前 64 字符
    3. tool_use_id 回退
    4. 字面量 "unknown"
    """
    if block.id:
        return f"{block.type}:{block.id}"

    text = block.thinking or block.text or ""
    if text:
        return f"{block.type}:{text[:64]}"

    return f"{block.type}:{block.tool_use_id or 'unknown'}"
```

### 5.4 parseDoubleEncodedResult 实现

原始逻辑：

```javascript
function parseDoubleEncodedResult(result) {
    if (result == null) return '';
    if (typeof result === 'string') {
        try {
            const parsed = JSON.parse(result);
            if (typeof parsed === 'string') return parsed;
        } catch { }
        return result;
    }
    return String(result);
}
```

Python 实现：

```python
def _parse_double_encoded_result(result: Any) -> str:
    """
    处理 Claude Code result 字段的双重 JSON 编码。
    对标 claude-code-parser parseDoubleEncodedResult()。

    例：result = '"actual text"'  →  返回 'actual text'
    例：result = 'plain text'     →  返回 'plain text'
    例：result = None             →  返回 ''
    """
    if result is None:
        return ""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, str):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return result
    return str(result)
```

### 5.5 extractContent 实现

原始逻辑：

```javascript
export function extractContent(raw) {
    if (raw == null) return '';
    if (typeof raw === 'string') return raw;
    if (Array.isArray(raw)) {
        const parts = [];
        for (const block of raw) {
            if (block && typeof block === 'object' && 'text' in block
                && typeof block.text === 'string') {
                if (block.text) parts.push(block.text);
            }
        }
        return parts.join('\n');
    }
    return String(raw);
}
```

Python 实现：

```python
def extract_content(raw: Any) -> str:
    """
    从多态 content 字段提取纯文本。
    对标 claude-code-parser extractContent()。

    处理三种形态：
    1. None → ''
    2. str → 原样返回
    3. list[dict] → 提取 .text 字段，以 \\n 连接
    4. 其他 → str(raw)
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if (isinstance(block, dict)
                    and "text" in block
                    and isinstance(block["text"], str)
                    and block["text"]):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(raw)
```

### 5.6 Translator 完整类实现

```python
import json
from typing import Any
from .protocol import ClaudeEvent, ClaudeContent
from .events import (
    RelayEvent, TextDeltaEvent, ThinkingDeltaEvent, ToolUseEvent,
    ToolResultEvent, SessionMetaEvent, TurnCompleteEvent, ErrorEvent,
)


class Translator:
    """
    有状态的 NDJSON 事件翻译器。
    对标 claude-code-parser Translator 类。

    将原始 ClaudeEvent 翻译为 RelayEvent 列表，
    内置 assistant 事件去重（通过 content index + block fingerprint）。
    """

    def __init__(self) -> None:
        self._last_content_index: int = 0
        self._last_first_block_key: str | None = None
        self._session_id: str | None = None
        self._model: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def model(self) -> str | None:
        return self._model

    def reset(self) -> None:
        """重置去重状态。对标 Translator.reset()。"""
        self._last_content_index = 0
        self._last_first_block_key = None

    def translate(self, raw: ClaudeEvent) -> list[RelayEvent]:
        """
        主分发入口。对标 Translator.translate()。
        根据 raw.type 路由到对应子方法。
        """
        match raw.type:
            case "system":
                return self._translate_system(raw)
            case "result":
                return self._translate_result(raw)
            case "assistant":
                return self._translate_assistant(raw)
            case "user":
                return self._translate_user(raw)
            case _:
                return []   # progress, rate_limit_event 等静默忽略

    # ── 私有翻译方法 ──────────────────────────────────────────

    def _translate_system(self, raw: ClaudeEvent) -> list[RelayEvent]:
        """对标 Translator.translateSystem()。"""
        match raw.subtype:
            case "init":
                if raw.session_id:
                    self._session_id = raw.session_id
                if raw.model:
                    self._model = raw.model
                return [SessionMetaEvent(model=raw.model or "unknown")]

            case "result":
                result_text = _parse_double_encoded_result(raw.result)
                self.reset()
                if raw.is_error:
                    return [ErrorEvent(
                        message=result_text,
                        session_id=raw.session_id,
                    )]
                return [TurnCompleteEvent(session_id=raw.session_id)]

            case _:
                return []

    def _translate_result(self, raw: ClaudeEvent) -> list[RelayEvent]:
        """
        对标 Translator.translateResult()。
        处理 result 事件，提取 usage 信息。
        注意：inputTokens = inputTokens + cacheReadInputTokens + cacheCreationInputTokens
        """
        result_text = _parse_double_encoded_result(raw.result)

        if raw.subtype == "error" or raw.is_error:
            self.reset()
            return [ErrorEvent(
                message=result_text,
                session_id=raw.session_id,
            )]

        event = TurnCompleteEvent(
            session_id=raw.session_id,
            cost_usd=raw.total_cost_usd,
        )

        # 从 modelUsage 提取 usage（取第一个模型条目）
        if raw.model_usage:
            for usage in raw.model_usage.values():
                event.input_tokens = (
                    usage.input_tokens
                    + usage.cache_read_input_tokens
                    + usage.cache_creation_input_tokens
                )
                event.output_tokens = usage.output_tokens
                event.context_window = usage.context_window
                break   # 只取第一个

        self.reset()
        return [event]

    def _translate_assistant(self, raw: ClaudeEvent) -> list[RelayEvent]:
        """
        对标 Translator.translateAssistant()。
        核心去重逻辑：通过 content index 和 block fingerprint 实现增量提取。
        """
        msg = raw.message
        if not msg or not msg.content:
            return []

        # 指纹检测上下文切换
        first_key = _block_fingerprint(msg.content[0])
        if first_key != self._last_first_block_key:
            self._last_content_index = 0
            self._last_first_block_key = first_key

        # 安全回退
        if len(msg.content) < self._last_content_index:
            self._last_content_index = 0

        events: list[RelayEvent] = []
        for i in range(self._last_content_index, len(msg.content)):
            block = msg.content[i]
            ev = self._translate_content_block(block)
            if ev is not None:
                events.append(ev)

        self._last_content_index = len(msg.content)
        return events

    def _translate_user(self, raw: ClaudeEvent) -> list[RelayEvent]:
        """对标 Translator.translateUser()。"""
        msg = raw.message
        if not msg or not msg.content:
            return []

        events: list[RelayEvent] = []
        for block in msg.content:
            if block.type == "tool_result":
                events.append(ToolResultEvent(
                    tool_use_id=block.tool_use_id or "",
                    output=extract_content(block.content),
                    is_error=block.is_error or False,
                ))
        return events

    def _translate_content_block(self, block: ClaudeContent) -> RelayEvent | None:
        """
        对标 Translator.translateContentBlock()。
        将单个 content block 翻译为 RelayEvent。
        """
        match block.type:
            case "text":
                return TextDeltaEvent(content=block.text or "")

            case "thinking":
                text = block.thinking or block.text or ""
                if not text:
                    return None
                return ThinkingDeltaEvent(content=text)

            case "tool_use":
                input_str = ""
                if block.input is not None:
                    input_str = json.dumps(block.input)
                return ToolUseEvent(
                    tool_use_id=block.id or "",
                    tool_name=block.name or "",
                    input=input_str,
                )

            case "tool_result":
                return ToolResultEvent(
                    tool_use_id=block.tool_use_id or "",
                    output=extract_content(block.content),
                    is_error=block.is_error or False,
                )

            case _:
                return None     # 未知 block 类型静默跳过
```

---

## 6. Writer — 消息构造器

### 6.1 原始逻辑（对标 writer.js）

```javascript
export const createMessage = {
    user(content) {
        return JSON.stringify({ type: 'user', message: { role: 'user', content } }) + '\n';
    },
    approve(toolUseId) {
        return JSON.stringify({ type: 'approve', tool_use_id: toolUseId }) + '\n';
    },
    deny(toolUseId) {
        return JSON.stringify({ type: 'deny', tool_use_id: toolUseId }) + '\n';
    },
    toolResult(toolUseId, content) {
        return JSON.stringify({ type: 'tool_result', tool_use_id: toolUseId, content }) + '\n';
    },
};
```

### 6.2 Python 实现

```python
import json


def create_user_message(content: str) -> str:
    """构造 user 消息 NDJSON 行。对标 createMessage.user()。"""
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": content},
    }) + "\n"


def create_approve_message(tool_use_id: str) -> str:
    """构造 approve 消息 NDJSON 行。对标 createMessage.approve()。"""
    return json.dumps({
        "type": "approve",
        "tool_use_id": tool_use_id,
    }) + "\n"


def create_deny_message(tool_use_id: str) -> str:
    """构造 deny 消息 NDJSON 行。对标 createMessage.deny()。"""
    return json.dumps({
        "type": "deny",
        "tool_use_id": tool_use_id,
    }) + "\n"


def create_tool_result_message(tool_use_id: str, content: str) -> str:
    """构造 tool_result 消息 NDJSON 行。对标 createMessage.toolResult()。"""
    return json.dumps({
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }) + "\n"
```

设计决策：JS 版用对象方法，Python 版用模块级函数——更符合 Python 惯例，import 更灵活。

---

## 7. 公开 API（__init__.py）

```python
"""
claude_code.parser — Claude Code CLI NDJSON 流解析器。
对标 claude-code-parser npm 包的完整功能对等 Python 实现。
"""

from .parse import parse_line
from .translator import Translator, extract_content
from .writer import (
    create_user_message,
    create_approve_message,
    create_deny_message,
    create_tool_result_message,
)
from .protocol import (
    ClaudeEvent,
    ClaudeMessage,
    ClaudeContent,
    ModelUsageEntry,
    claude_event_from_dict,
)
from .events import (
    RelayEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolUseEvent,
    ToolResultEvent,
    SessionMetaEvent,
    TurnCompleteEvent,
    ErrorEvent,
)

__all__ = [
    # 函数
    "parse_line",
    "extract_content",
    "create_user_message",
    "create_approve_message",
    "create_deny_message",
    "create_tool_result_message",
    # 类
    "Translator",
    # 线路格式类型
    "ClaudeEvent",
    "ClaudeMessage",
    "ClaudeContent",
    "ModelUsageEntry",
    "claude_event_from_dict",
    # 事件类型
    "RelayEvent",
    "TextDeltaEvent",
    "ThinkingDeltaEvent",
    "ToolUseEvent",
    "ToolResultEvent",
    "SessionMetaEvent",
    "TurnCompleteEvent",
    "ErrorEvent",
]
```

---

## 8. 测试策略

### 8.1 逐函数对照测试矩阵

| 测试场景 | 对标原始行为 | 文件 |
|---------|------------|------|
| `parse_line("")` → None | 空行返回 null | test_parse.py |
| `parse_line("not json")` → None | 非 JSON 静默跳过 | test_parse.py |
| `parse_line('{"type":"result"}')` → ClaudeEvent | 正常解析 | test_parse.py |
| `parse_line(stream_event)` 保留 `event` payload | session 二层去重依赖 `ClaudeEvent.event` | test_parse.py |
| `parse_line(system.api_retry)` 保留 retry 扩展字段 | stdout FailFast 依赖 `attempt/max_retries/...` | test_parse.py |
| `parse_line(assistant with message.id)` 保留 `message.id` | stream_event / assistant snapshot 去重依赖 message ID | test_parse.py |
| Translator + system.init → SessionMetaEvent | 捕获 session_id + model | test_translator.py |
| Translator + result.success → TurnCompleteEvent + usage 聚合 | inputTokens = sum of 3 fields | test_translator.py |
| Translator + result.error → ErrorEvent | is_error 或 subtype=error | test_translator.py |
| Translator + assistant 去重：连续 3 个累积快照 | 只发送新增 block | test_translator.py |
| Translator + assistant 上下文切换 | fingerprint 变化时重置 index | test_translator.py |
| Translator + assistant 内容缩短 | 安全回退重置 index | test_translator.py |
| Translator + user tool_result | 提取 tool_use_id + content | test_translator.py |
| translateContentBlock 各类型 | text/thinking/tool_use/tool_result/unknown | test_translator.py |
| extractContent(None) → "" | 空值处理 | test_extract_content.py |
| extractContent("str") → "str" | 字符串直通 | test_extract_content.py |
| extractContent([{text:"a"},{text:"b"}]) → "a\nb" | 数组提取 | test_extract_content.py |
| extractContent(12345) → "12345" | 回退 str() | test_extract_content.py |
| parseDoubleEncodedResult('"text"') → "text" | 双重编码解包 | test_translator.py |
| parseDoubleEncodedResult("plain") → "plain" | 非双重编码直通 | test_translator.py |
| parseDoubleEncodedResult(None) → "" | 空值处理 | test_translator.py |
| blockFingerprint + id 优先 | type:id | test_translator.py |
| blockFingerprint + text 截断 64 字符 | type:text[:64] | test_translator.py |
| blockFingerprint + tool_use_id 回退 | type:tool_use_id | test_translator.py |
| writer 各消息格式 | JSON + 换行 | test_writer.py |

### 8.2 测试示例

```python
def test_translator_dedup_incremental():
    """验证 assistant 事件的增量去重。"""
    t = Translator()

    # 第一次：1 个 text block
    ev1 = ClaudeEvent(
        type="assistant",
        message=ClaudeMessage(content=[
            ClaudeContent(type="text", text="Hello"),
        ]),
    )
    result1 = t.translate(ev1)
    assert len(result1) == 1
    assert result1[0].type == "text_delta"
    assert result1[0].content == "Hello"

    # 第二次：2 个 block（累积快照）
    ev2 = ClaudeEvent(
        type="assistant",
        message=ClaudeMessage(content=[
            ClaudeContent(type="text", text="Hello"),
            ClaudeContent(type="text", text=" World"),
        ]),
    )
    result2 = t.translate(ev2)
    assert len(result2) == 1       # 只有新增的第二个
    assert result2[0].content == " World"

    # 第三次：3 个 block
    ev3 = ClaudeEvent(
        type="assistant",
        message=ClaudeMessage(content=[
            ClaudeContent(type="text", text="Hello"),
            ClaudeContent(type="text", text=" World"),
            ClaudeContent(type="tool_use", id="tool_1", name="Read",
                          input={"file_path": "/tmp/test.txt"}),
        ]),
    )
    result3 = t.translate(ev3)
    assert len(result3) == 1       # 只有新增的 tool_use
    assert result3[0].type == "tool_use"


def test_translator_context_switch():
    """验证 blockFingerprint 变化时的重置行为。"""
    t = Translator()

    # Agent A 的消息
    ev_a = ClaudeEvent(
        type="assistant",
        message=ClaudeMessage(content=[
            ClaudeContent(type="text", text="Agent A response"),
        ]),
    )
    t.translate(ev_a)

    # Agent B 的消息（第一个 block 指纹不同 → 重置）
    ev_b = ClaudeEvent(
        type="assistant",
        message=ClaudeMessage(content=[
            ClaudeContent(type="text", text="Agent B response"),
        ]),
    )
    result = t.translate(ev_b)
    assert len(result) == 1
    assert result[0].content == "Agent B response"
```

---

## 9. 与上层 SDK 的集成点

parser 子包作为纯函数库被上层 `claude_code` SDK 消费：

```
claude_code/
  _session.py    ← import Translator, parse_line, extract_content
  _exec.py       ← import create_user_message (构造 stdin 消息)
  parser/        ← 本方案的独立子包
```

上层 `_session.py` 的使用模式：

```python
from claude_code.parser import Translator, parse_line

translator = Translator()
for line in process.stdout:
    event = parse_line(line)
    if event is None:
        continue
    relay_events = translator.translate(event)
    for relay_event in relay_events:
        yield relay_event
```

注意：`parse_line()` **不能**吞掉 `message.id`、`stream_event.event` 与
`system.api_retry` 扩展字段。它们虽然不属于 parser 自己的翻译职责，但会在上层
`_session.py` 中被第二层去重与 FailFast 逻辑直接消费。

---

## 10. 设计约束与对标合规检查清单

- [ ] `parseLine` 对空行返回 None，对非 JSON 返回 None（不抛异常）
- [ ] `Translator.translate` 对未知 type 返回空列表（不抛异常）
- [ ] `translateContentBlock` 对未知 block type 返回 None（不抛异常）
- [ ] `translateAssistant` 的去重逻辑与原始 JS 完全对等（index + fingerprint）
- [ ] `blockFingerprint` 优先级：id → text[:64] → tool_use_id → "unknown"
- [ ] `parseDoubleEncodedResult` 仅在 JSON.parse 结果为 str 时解包
- [ ] `extractContent` 处理 None / str / list / 其他 四种形态
- [ ] `translateResult` 中 inputTokens = input + cacheRead + cacheCreation
- [ ] `translateResult` 中 modelUsage 取第一个模型条目后 break
- [ ] Writer 4 个函数各自生成正确的 JSON + '\n' 格式
- [ ] `ClaudeMessage.id` 必须保留，供 session 层 message ID 识别
- [ ] `ClaudeEvent.event` 必须保留，供 `stream_event` envelope 透传
- [ ] `system.api_retry` 扩展字段必须保留：`attempt/max_retries/retry_delay_ms/error_status/error`
- [ ] `ClaudeEvent.raw` 必须保留原始 dict，避免未来协议扩展字段被吞掉
- [ ] 所有 snake_case 字段名与 JSON 线路格式一致
- [ ] 零外部依赖（仅 stdlib json）
