# claude-code-python 技术设计方案

> 严格对标 [`agent-sdk`](../../agent-sdk/) 的权威 TypeScript 实现（`agent-sdk/src/*.ts`），
> 功能一对一 Python 移植。
> parser 子包细节见 [docs/parser/design.md](./parser/design.md)。  
> E2E 详细设计见 [docs/e2e/design.md](./e2e/design.md)。

---

## 1. 项目定位

### 核心思路

本 SDK **不调用 `@anthropic-ai/claude-code` 的 Node API**，而是通过
`asyncio.create_subprocess_exec` spawn Claude Code CLI 子进程，以
`--output-format stream-json --verbose` 模式运行，按行读取 NDJSON 输出，
翻译为结构化 Python 类型。

```text
业务代码
  ↓
ClaudeCode (入口 + 全局配置)            ← 对标 agent-sdk/src/claude-code.ts
  ↓
Session (会话状态机 + 事件翻译)         ← 对标 agent-sdk/src/session.ts
  ↓
ClaudeCodeExec (进程管理 + 参数构建)    ← 对标 agent-sdk/src/exec.ts
  ↓
asyncio.create_subprocess_exec("claude", args...)
  ↓
NDJSON stdout → claude_code.parser.Translator → RelayEvent
```

### 功能范围（与 agent-sdk 完全对等）

| 能力 | 对应 agent-sdk 源 |
|------|------------------|
| 全局配置（cliPath/env/apiKey/authToken/baseUrl） | `src/options.ts` + `src/claude-code.ts` |
| 35+ CLI 参数映射 | `src/exec.ts` `buildArgs()` |
| 会话管理（start / resume / continue，自动 `--resume`） | `src/session.ts` `Session` + `_hasRun` 状态机 |
| 缓冲模式 `run()` 返回 `Turn` | `src/session.ts` `run()` |
| 流式模式 `run_streamed()` 返回 `AsyncIterator[RelayEvent]` | `src/session.ts` `runStreamed()` + `createEventChannel` |
| 双层流去重（parser + session） | `src/session.ts` `translateRelayEvents` / `suppressDuplicateAssistantSnapshot` |
| 图片输入（magic bytes → base64 → stream-json stdin） | `src/exec.ts` `buildStdinPayload` / `detectImageMediaType` |
| 结构化输出（`--json-schema` → `Turn.structured_output`） | `src/session.ts` `StreamProcessResult.structuredOutput` |
| FailFast（stderr `API Error:` + stdout `system.api_retry`） | `src/session.ts` `extractFatalCliApiError*` |
| 环境隔离（不继承 `os.environ`，仅显式 env + PATH） | `src/exec.ts` spawn 逻辑 |
| 原始事件日志（NDJSON 文件） | `src/raw-event-log.ts` |

---

## 2. 仓库结构

```text
claude-code-python/
├── pyproject.toml
├── README.md
├── LICENSE
├── .github/workflows/ci.yml
├── src/
│   └── claude_code/
│       ├── __init__.py             # 公开 API；从 parser re-export RelayEvent 等
│       ├── _client.py              # ClaudeCode 入口类 ← claude-code.ts
│       ├── _session.py             # Session 状态机 + EventChannel ← session.ts
│       ├── _exec.py                # ClaudeCodeExec + buildArgs + stdin ← exec.ts
│       ├── _options.py             # SDK 层类型（Options / Turn / RawClaudeEvent）
│       ├── _raw_event_log.py       # NDJSON 落盘 ← raw-event-log.ts
│       ├── parser/                 # 独立子包，见 docs/parser/design.md
│       │   ├── __init__.py
│       │   ├── protocol.py
│       │   ├── events.py
│       │   ├── parse.py
│       │   ├── translator.py
│       │   └── writer.py
│       └── py.typed
├── tests/
│   ├── fixtures/
│   │   └── fake_claude.py          # fake CLI（对齐 agent-sdk/tests/unit/fixtures/fake-claude.mjs）
│   ├── unit/
│   │   ├── test_exec.py
│   │   ├── test_session.py
│   │   ├── test_raw_event_log.py
│   │   └── parser/                 # parser 子包单测
│   │       ├── test_parse.py
│   │       ├── test_translator.py
│   │       └── test_writer.py
│   └── e2e/
│       ├── conftest.py
│       ├── config.py
│       ├── harness.py
│       ├── reporters.py
│       ├── fixtures/
│       │   └── images/
│       │       ├── red-square.png
│       │       ├── shapes-demo.png
│       │       └── receipt-demo.png
│       └── cases/
│           └── test_real_cli.py
└── docs/
    ├── design.md                   # 本文件
    ├── parser/design.md
    └── e2e/design.md
```

SDK 层只关心 SDK，parser 层的类型定义与实现全部收敛到 `claude_code.parser`。`_options.py`
**不得**重复定义 `ClaudeEvent / ClaudeMessage / ClaudeContent / RelayEvent / Translator / parse_line`，
统一从 `claude_code.parser` 导入。

---

## 3. 类型系统（SDK 层，`_options.py`）

严格对齐 [agent-sdk/src/options.ts](../../agent-sdk/src/options.ts) 与
[agent-sdk/src/session.ts](../../agent-sdk/src/session.ts) 的公开类型。

### 3.1 `ClaudeCodeOptions` — 对应 `options.ts` L4–L15

```python
from dataclasses import dataclass

@dataclass
class ClaudeCodeOptions:
    cli_path: str | None = None          # cliPath
    env: dict[str, str] | None = None
    api_key: str | None = None           # apiKey → ANTHROPIC_API_KEY
    auth_token: str | None = None        # authToken → ANTHROPIC_AUTH_TOKEN
    base_url: str | None = None          # baseUrl → ANTHROPIC_BASE_URL
```

仅 5 个字段，与 agent-sdk 完全对等。

### 3.2 `PermissionMode` / `Effort`

```python
from typing import Literal

PermissionMode = Literal[
    "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"
]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
```

### 3.3 `AgentDefinition` — 对应 `options.ts` L40–L53

```python
@dataclass
class AgentDefinition:
    description: str | None = None
    prompt: str | None = None
    tools: list[str] | None = None
    allowed_tools: list[str] | None = None        # allowedTools
    disallowed_tools: list[str] | None = None     # disallowedTools
    model: str | None = None
    effort: Effort | None = None
    max_turns: int | None = None                  # maxTurns
    permission_mode: PermissionMode | None = None # permissionMode
    isolation: Literal["worktree"] | None = None
    initial_prompt: str | None = None             # initialPrompt
    mcp_servers: dict[str, Any] | None = None     # mcpServers
```

### 3.4 `SessionOptions` — 对应 `options.ts` L56–L142

35+ 字段完全对齐，三态字段（`verbose / include_partial_messages / chrome`）使用
`bool | None`；联合字段使用 `str | list[str]` / `str | dict` / `bool | str`。

```python
@dataclass
class SessionOptions:
    # --- Model / Context ---
    model: str | None = None
    cwd: str | None = None
    additional_directories: list[str] | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None

    # --- System prompt ---
    system_prompt: str | None = None
    system_prompt_file: str | None = None
    append_system_prompt: str | None = None
    append_system_prompt_file: str | None = None

    # --- Permissions ---
    permission_mode: PermissionMode | None = None
    dangerously_skip_permissions: bool = False
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    tools: str | None = None
    permission_prompt_tool: str | None = None

    # --- MCP ---
    mcp_config: str | list[str] | None = None
    strict_mcp_config: bool = False

    # --- Model tuning ---
    effort: Effort | None = None
    fallback_model: str | None = None

    # --- Runtime flags ---
    bare: bool = False
    no_session_persistence: bool = False
    chrome: bool | None = None                      # None=不传, True=--chrome, False=--no-chrome

    # --- Agents ---
    agents: dict[str, AgentDefinition] | str | None = None
    agent: str | None = None

    # --- Metadata ---
    name: str | None = None

    # --- Settings ---
    settings: str | None = None
    setting_sources: str | None = None              # agent-sdk 默认传 ""；None 时仍然传 ""

    # --- Output ---
    verbose: bool | None = None                     # None → 默认 True（传 --verbose）
    include_partial_messages: bool | None = None    # None → 默认 True
    include_hook_events: bool = False

    # --- Misc ---
    betas: str | None = None
    worktree: str | None = None
    disable_slash_commands: bool = False
    plugin_dir: str | list[str] | None = None
    exclude_dynamic_system_prompt_sections: bool = False
    debug: str | bool | None = None
    debug_file: str | None = None

    # --- Structured output ---
    json_schema: str | dict[str, Any] | None = None

    # --- Session identity ---
    session_id: str | None = None
    fork_session: bool = False

    # --- Raw event log ---
    raw_event_log: bool | str | None = None         # 对应 rawEventLog: boolean | string
```

### 3.5 `TurnOptions` — 对应 `options.ts` L145–L155

```python
from typing import Callable

@dataclass
class TurnOptions:
    cancel_event: asyncio.Event | None = None                            # signal
    on_raw_event: Callable[["RawClaudeEvent"], None] | None = None       # onRawEvent
    fail_fast_on_cli_api_error: bool = False                             # failFastOnCliApiError
```

取消通道使用 `asyncio.Event`，等价于 agent-sdk 的 `AbortSignal`；若调用方所在协程已经
被 `asyncio.CancelledError` 传播，Session 也必须同时响应（见第 6.5 节）。

### 3.6 `RawClaudeEvent` — 对应 `options.ts` L29–L37

**严格 7 种**，与 agent-sdk 一致（注意：没有 `stdout_chunk`）：

```python
from typing import Literal, Union

@dataclass
class SpawnEvent:
    type: Literal["spawn"] = "spawn"
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None

@dataclass
class StdinClosedEvent:
    type: Literal["stdin_closed"] = "stdin_closed"

@dataclass
class StdoutLineEvent:
    type: Literal["stdout_line"] = "stdout_line"
    line: str = ""

@dataclass
class StderrChunkEvent:
    type: Literal["stderr_chunk"] = "stderr_chunk"
    chunk: str = ""

@dataclass
class StderrLineEvent:
    type: Literal["stderr_line"] = "stderr_line"
    line: str = ""

@dataclass
class ProcessErrorEvent:
    type: Literal["process_error"] = "process_error"
    error: BaseException | None = None   # 序列化为 {name, message, stack(traceback)}

@dataclass
class ExitEvent:
    type: Literal["exit"] = "exit"
    code: int | None = None
    signal: str | None = None            # 进程退出信号名（如 "SIGTERM"）

RawClaudeEvent = Union[
    SpawnEvent, StdinClosedEvent, StdoutLineEvent,
    StderrChunkEvent, StderrLineEvent, ProcessErrorEvent, ExitEvent,
]
```

### 3.7 返回类型 — 对应 `session.ts` L27–L48

```python
@dataclass
class TurnUsage:
    cost_usd: float | None = None        # costUsd
    input_tokens: int | None = None      # inputTokens
    output_tokens: int | None = None     # outputTokens
    context_window: int | None = None    # contextWindow

@dataclass
class Turn:
    events: list["RelayEvent"]
    final_response: str                  # finalResponse
    usage: TurnUsage | None
    session_id: str | None               # sessionId
    structured_output: Any               # structuredOutput

RunResult = Turn

@dataclass
class StreamedTurn:
    events: "AsyncIterator[RelayEvent]"

RunStreamedResult = StreamedTurn
```

### 3.8 输入类型 — 对应 `session.ts` L19–L23

```python
from typing import TypedDict

class UserTextInput(TypedDict):
    type: Literal["text"]
    text: str

class UserLocalImageInput(TypedDict):
    type: Literal["local_image"]
    path: str

UserInput = UserTextInput | UserLocalImageInput
Input = str | list[UserInput]
```

### 3.9 公开 API 导出（`__init__.py`）

对标 [agent-sdk/src/index.ts](../../agent-sdk/src/index.ts) 的 re-export 边界：

```python
# SDK 类
from ._client import ClaudeCode
from ._session import Session

# SDK 类型
from ._options import (
    ClaudeCodeOptions, SessionOptions, TurnOptions,
    PermissionMode, Effort, AgentDefinition,
    SpawnEvent, StdinClosedEvent, StdoutLineEvent,
    StderrChunkEvent, StderrLineEvent, ProcessErrorEvent, ExitEvent,
    RawClaudeEvent,
)
from ._session import (
    Input, UserInput, UserTextInput, UserLocalImageInput,
    Turn, TurnUsage, RunResult, StreamedTurn, RunStreamedResult,
)

# parser 层 re-export（与 agent-sdk/src/index.ts L27–L42 对应）
from .parser import (
    RelayEvent, TextDeltaEvent, ThinkingDeltaEvent, ToolUseEvent, ToolResultEvent,
    SessionMetaEvent, TurnCompleteEvent, ErrorEvent,
    ClaudeEvent, ClaudeMessage, ClaudeContent, ModelUsageEntry,
    parse_line, Translator, extract_content,
)
```

---

## 4. 入口类 `_client.py` — 对标 `claude-code.ts`

```python
from ._exec import ClaudeCodeExec
from ._options import ClaudeCodeOptions, SessionOptions
from ._session import Session

class ClaudeCode:
    """SDK 主入口，对标 agent-sdk `ClaudeCode`。"""

    def __init__(self, options: ClaudeCodeOptions | None = None) -> None:
        opts = options or ClaudeCodeOptions()
        normalized_env = _merge_claude_env(opts)
        self._options = ClaudeCodeOptions(
            cli_path=opts.cli_path,
            env=normalized_env,
            api_key=opts.api_key,
            auth_token=opts.auth_token,
            base_url=opts.base_url,
        )
        self._exec = ClaudeCodeExec(
            cli_path=opts.cli_path,
            env=normalized_env,
        )

    def start_session(self, options: SessionOptions | None = None) -> Session:
        return Session(self._exec, self._options, options or SessionOptions())

    def resume_session(
        self, session_id: str, options: SessionOptions | None = None
    ) -> Session:
        return Session(
            self._exec, self._options, options or SessionOptions(),
            session_id=session_id,
        )

    def continue_session(self, options: SessionOptions | None = None) -> Session:
        return Session(
            self._exec, self._options, options or SessionOptions(),
            session_id=None, continue_mode=True,
        )


def _merge_claude_env(opts: ClaudeCodeOptions) -> dict[str, str]:
    """对标 mergeClaudeEnv（claude-code.ts L50–L68）。"""
    env: dict[str, str] = dict(opts.env or {})
    if opts.api_key is not None:
        env["ANTHROPIC_API_KEY"] = opts.api_key
    if opts.auth_token is not None:
        env["ANTHROPIC_AUTH_TOKEN"] = opts.auth_token
    if opts.base_url is not None:
        env["ANTHROPIC_BASE_URL"] = opts.base_url
    return env
```

---

## 5. 进程管理 `_exec.py` — 对标 `exec.ts`

### 5.1 `ClaudeCodeExec`

```python
class ClaudeCodeExec:
    def __init__(
        self,
        cli_path: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._cli_path = cli_path                # 延迟解析，避免阻塞事件循环
        self._env_override: dict[str, str] = env or {}
        self._default_cli_path: str | None = None

    async def _resolve_cli_path(self) -> str:
        if self._cli_path:
            return self._cli_path
        if self._default_cli_path is None:
            self._default_cli_path = await asyncio.to_thread(_resolve_default_cli_path)
        return self._default_cli_path

    async def run(self, args: "ExecArgs") -> None:
        ...
```

### 5.2 CLI 路径解析（延迟 + 线程化）

agent-sdk 中 `resolveDefaultCliPath()`（exec.ts L42–L48）通过 `createRequire` 查找
`@anthropic-ai/claude-code/cli.js`；找不到则回退 `"claude"`。Python 侧用 `node -e` 探测，
并放进 `asyncio.to_thread` 里避免阻塞事件循环：

```python
def _resolve_default_cli_path() -> str:
    try:
        proc = subprocess.run(
            ["node", "-e",
             "try{console.log(require.resolve('@anthropic-ai/claude-code/cli.js'))}"
             "catch{process.exit(1)}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "claude"
```

### 5.3 `ExecArgs`

对标 exec.ts L9–L38：

```python
@dataclass
class ExecArgs:
    input: str
    input_items: list[UserInput] | None = None
    images: list[str] | None = None

    resume_session_id: str | None = None
    continue_session: bool = False

    session_options: SessionOptions | None = None

    cli_path: str | None = None
    env: dict[str, str] | None = None

    cancel_event: asyncio.Event | None = None
    raw_event_logger: "RawEventLogger | None" = None
    on_raw_event: Callable[[RawClaudeEvent], None] | None = None
    on_line: Callable[[str], None] | None = None
```

### 5.4 `build_args()` — 一一对齐 exec.ts `buildArgs()`

必须逐字对齐 exec.ts L194–L419 的顺序与条件：

```python
def build_args(
    args: ExecArgs,
    *,
    use_stream_json_input: bool,
) -> list[str]:
    cmd: list[str] = []
    opts = args.session_options or SessionOptions()

    # --- Prompt ---
    cmd.append("-p")
    if not use_stream_json_input:
        cmd.append(args.input)

    if use_stream_json_input:
        cmd += ["--input-format", "stream-json"]

    # --- Output format ---
    cmd += ["--output-format", "stream-json"]

    # --- Verbose（None/True 都传，只有显式 False 不传）---
    if opts.verbose is not False:
        cmd.append("--verbose")

    # --- Include partial messages（默认 True）---
    if opts.include_partial_messages is not False:
        cmd.append("--include-partial-messages")

    # --- Session management ---
    if args.continue_session:
        cmd.append("--continue")
    elif args.resume_session_id:
        cmd += ["--resume", args.resume_session_id]

    if opts.session_id:
        cmd += ["--session-id", opts.session_id]
    if opts.fork_session:
        cmd.append("--fork-session")

    # --- Model ---
    if opts.model:
        cmd += ["--model", opts.model]

    # --- Additional directories ---
    for d in (opts.additional_directories or []):
        cmd += ["--add-dir", d]

    # --- Max turns / budget ---
    if opts.max_turns is not None:
        cmd += ["--max-turns", str(opts.max_turns)]
    if opts.max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(opts.max_budget_usd)]

    # --- System prompt ---
    if opts.system_prompt:
        cmd += ["--system-prompt", opts.system_prompt]
    elif opts.system_prompt_file:
        cmd += ["--system-prompt-file", opts.system_prompt_file]
    if opts.append_system_prompt:
        cmd += ["--append-system-prompt", opts.append_system_prompt]
    if opts.append_system_prompt_file:
        cmd += ["--append-system-prompt-file", opts.append_system_prompt_file]

    # --- Permissions（skip 优先）---
    if opts.dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    elif opts.permission_mode:
        cmd += ["--permission-mode", opts.permission_mode]

    for tool in (opts.allowed_tools or []):
        cmd += ["--allowedTools", tool]
    for tool in (opts.disallowed_tools or []):
        cmd += ["--disallowedTools", tool]
    if opts.tools is not None:
        cmd += ["--tools", opts.tools]
    if opts.permission_prompt_tool:
        cmd += ["--permission-prompt-tool", opts.permission_prompt_tool]

    # --- MCP ---
    mcp_configs = (
        [opts.mcp_config] if isinstance(opts.mcp_config, str)
        else (opts.mcp_config or [])
    )
    for cfg in mcp_configs:
        cmd += ["--mcp-config", cfg]
    if opts.strict_mcp_config:
        cmd.append("--strict-mcp-config")

    # --- Effort / fallback ---
    if opts.effort:
        cmd += ["--effort", opts.effort]
    if opts.fallback_model:
        cmd += ["--fallback-model", opts.fallback_model]

    # --- Bare / no session persistence ---
    if opts.bare:
        cmd.append("--bare")
    if opts.no_session_persistence:
        cmd.append("--no-session-persistence")

    # --- Chrome ---
    if opts.chrome is True:
        cmd.append("--chrome")
    elif opts.chrome is False:
        cmd.append("--no-chrome")

    # --- Agents ---
    if opts.agents is not None:
        agents_str = (
            opts.agents if isinstance(opts.agents, str)
            else json.dumps(opts.agents, default=_dataclass_to_dict)
        )
        cmd += ["--agents", agents_str]
    if opts.agent:
        cmd += ["--agent", opts.agent]

    # --- Name ---
    if opts.name:
        cmd += ["--name", opts.name]

    # --- Settings ---
    if opts.settings is not None:
        cmd += ["--settings", opts.settings]

    # --- Setting sources（agent-sdk 默认总是传 ""，见 exec.ts L361–L362）---
    cmd += [
        "--setting-sources",
        opts.setting_sources if opts.setting_sources is not None else "",
    ]

    # --- Hook events ---
    if opts.include_hook_events:
        cmd.append("--include-hook-events")

    # --- Betas ---
    if opts.betas:
        cmd += ["--betas", opts.betas]

    # --- Worktree ---
    if opts.worktree:
        cmd += ["--worktree", opts.worktree]

    # --- Disable slash commands ---
    if opts.disable_slash_commands:
        cmd.append("--disable-slash-commands")

    # --- Plugin dir ---
    plugin_dirs = (
        [opts.plugin_dir] if isinstance(opts.plugin_dir, str)
        else (opts.plugin_dir or [])
    )
    for d in plugin_dirs:
        cmd += ["--plugin-dir", d]

    # --- Exclude dynamic system prompt sections ---
    if opts.exclude_dynamic_system_prompt_sections:
        cmd.append("--exclude-dynamic-system-prompt-sections")

    # --- Debug ---
    if opts.debug is True:
        cmd.append("--debug")
    elif isinstance(opts.debug, str):
        cmd += ["--debug", opts.debug]
    if opts.debug_file:
        cmd += ["--debug-file", opts.debug_file]

    # --- JSON Schema ---
    if opts.json_schema is not None:
        schema_str = (
            opts.json_schema if isinstance(opts.json_schema, str)
            else json.dumps(opts.json_schema)
        )
        cmd += ["--json-schema", schema_str]

    return cmd
```

### 5.5 `build_stdin_payload()` / `detect_image_media_type()` — 对齐 exec.ts L421–L564

```python
async def build_stdin_payload(args: ExecArgs) -> str | None:
    items = _get_structured_input_items(args)
    if items is None:
        return None

    content: list[dict[str, Any]] = []
    for item in items:
        if item["type"] == "text":
            if item["text"]:
                content.append({"type": "text", "text": item["text"]})
            continue
        data = await asyncio.to_thread(Path(item["path"]).read_bytes)
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": detect_image_media_type(data, item["path"]),
                "data": base64.b64encode(data).decode("ascii"),
            },
        })

    if not content:
        return None

    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": content},
    }) + "\n"


def _get_structured_input_items(args: ExecArgs) -> list[UserInput] | None:
    if args.input_items and any(i["type"] == "local_image" for i in args.input_items):
        return _merge_text_items(args.input_items)
    if args.images:
        items: list[UserInput] = []
        if args.input:
            items.append({"type": "text", "text": args.input})
        for p in args.images:
            items.append({"type": "local_image", "path": p})
        return items
    return None


def _merge_text_items(items: list[UserInput]) -> list[UserInput]:
    """连续 text 用 '\\n\\n' 合并，对齐 exec.ts mergeTextItems。"""
    merged: list[UserInput] = []
    pending: list[str] = []
    def flush() -> None:
        if pending:
            merged.append({"type": "text", "text": "\n\n".join(pending)})
            pending.clear()
    for item in items:
        if item["type"] == "text":
            pending.append(item["text"])
            continue
        flush()
        merged.append(item)
    flush()
    return merged


def detect_image_media_type(data: bytes, file_path: str) -> str:
    if len(data) >= 8 and data[:4] == b"\x89PNG":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 6 and data[:3] == b"GIF":
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    ext = Path(file_path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")
```

### 5.6 进程执行主流程

```python
async def run(self, args: ExecArgs) -> None:
    stdin_payload = await build_stdin_payload(args)
    cmd_args = build_args(args, use_stream_json_input=stdin_payload is not None)

    env: dict[str, str] = {**self._env_override, **(args.env or {})}
    if "PATH" not in env and "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]

    cli_path = args.cli_path or await self._resolve_cli_path()

    logger = args.raw_event_logger
    on_raw = args.on_raw_event
    def emit_raw(event: RawClaudeEvent) -> None:
        if logger is not None:
            logger.log(event)
        if on_raw is not None:
            on_raw(event)

    # --- spawn ---
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=args.session_options.cwd if args.session_options else None,
            env=env,
        )
    except OSError as e:
        emit_raw(ProcessErrorEvent(error=e))
        raise

    emit_raw(SpawnEvent(
        command=cli_path,
        args=list(cmd_args),
        cwd=args.session_options.cwd if args.session_options else None,
    ))

    # --- write stdin ---
    try:
        if stdin_payload is not None:
            proc.stdin.write(stdin_payload.encode("utf-8"))
            await proc.stdin.drain()           # 防止大 base64 阻塞
        proc.stdin.close()
        await proc.stdin.wait_closed()
    except Exception:
        pass
    emit_raw(StdinClosedEvent())

    stderr_chunks: list[str] = []

    async def _pump_stderr() -> None:
        # 同时发 stderr_chunk 与 stderr_line，对齐 exec.ts L118–L137
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            stderr_chunks.append(text)
            emit_raw(StderrChunkEvent(chunk=text))
            for line in text.splitlines():
                if line:
                    emit_raw(StderrLineEvent(line=line))

    stderr_task = asyncio.create_task(_pump_stderr())
    cancel_task = None
    if args.cancel_event is not None:
        cancel_task = asyncio.create_task(_watch_cancel(args.cancel_event, proc))

    try:
        while True:
            raw_line = await proc.stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8").rstrip("\n")
            emit_raw(StdoutLineEvent(line=line))
            if args.on_line is not None:
                args.on_line(line)

        await stderr_task
        return_code = await proc.wait()
        emit_raw(ExitEvent(code=return_code, signal=None))

        if return_code != 0:
            stderr_text = "".join(stderr_chunks)
            detail = f"code {return_code}"
            raise RuntimeError(
                f"Claude CLI exited with {detail}"
                + (f": {stderr_text}" if stderr_text else "")
            )
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
        stderr_task.cancel()
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
```

取消辅助：

```python
async def _watch_cancel(event: asyncio.Event, proc: asyncio.subprocess.Process) -> None:
    try:
        await event.wait()
    except asyncio.CancelledError:
        return
    if proc.returncode is None:
        proc.terminate()
```

---

## 6. Session 状态机 `_session.py` — 对标 `session.ts`

### 6.1 `EventChannel`（对标 `createEventChannel`，session.ts L63–L117）

```python
_SENTINEL = object()

class EventChannel(AsyncIterator["RelayEvent"]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._error: BaseException | None = None

    def push(self, event: "RelayEvent") -> None:
        self._queue.put_nowait(event)

    def end(self) -> None:
        self._queue.put_nowait(_SENTINEL)

    def set_error(self, err: BaseException) -> None:
        self._error = err
        self._queue.put_nowait(_SENTINEL)

    def __aiter__(self) -> "EventChannel":
        return self

    async def __anext__(self) -> "RelayEvent":
        item = await self._queue.get()
        if item is _SENTINEL:
            if self._error is not None:
                raise self._error
            raise StopAsyncIteration
        return item
```

### 6.2 `Session`

```python
from claude_code.parser import (
    RelayEvent, TextDeltaEvent, ThinkingDeltaEvent,
    TurnCompleteEvent, ErrorEvent,
    ClaudeEvent, Translator, parse_line,
)

class Session:
    """对标 agent-sdk `Session`。"""

    def __init__(
        self,
        exec: ClaudeCodeExec,
        global_options: ClaudeCodeOptions,
        session_options: SessionOptions,
        session_id: str | None = None,
        continue_mode: bool = False,
    ) -> None:
        self._exec = exec
        self._global_options = global_options
        self._session_options = session_options
        self._id = session_id
        self._continue_mode = continue_mode
        self._has_run = False
        self._active_tasks: set[asyncio.Task[Any]] = set()  # 防 run_streamed 任务被 GC

    @property
    def id(self) -> str | None:
        return self._id

    async def run(
        self, input: Input, turn_options: TurnOptions | None = None
    ) -> Turn:
        turn_options = turn_options or TurnOptions()
        events: list[RelayEvent] = []
        final_response = ""
        usage: TurnUsage | None = None
        session_id = self._id
        stream_error: ErrorEvent | None = None
        structured_output_ref: list[Any] = [None]

        def on_event(event: RelayEvent) -> None:
            nonlocal final_response, usage, session_id, stream_error
            events.append(event)
            if isinstance(event, TextDeltaEvent):
                final_response += event.content
            elif isinstance(event, TurnCompleteEvent):
                if event.session_id:
                    session_id = event.session_id
                usage = TurnUsage(
                    cost_usd=event.cost_usd,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    context_window=event.context_window,
                )
            elif isinstance(event, ErrorEvent):
                stream_error = event
                if event.session_id:
                    session_id = event.session_id

        await self._process_stream(input, turn_options, structured_output_ref, on_event)

        if stream_error is not None:
            raise RuntimeError(stream_error.message)

        return Turn(
            events=events,
            final_response=final_response,
            usage=usage,
            session_id=session_id,
            structured_output=structured_output_ref[0],
        )

    async def run_streamed(
        self, input: Input, turn_options: TurnOptions | None = None
    ) -> StreamedTurn:
        turn_options = turn_options or TurnOptions()
        channel = EventChannel()
        structured_output_ref: list[Any] = [None]

        async def _runner() -> None:
            try:
                await self._process_stream(
                    input, turn_options, structured_output_ref, channel.push,
                )
                channel.end()
            except BaseException as e:
                channel.set_error(e if isinstance(e, Exception) else RuntimeError(str(e)))

        task = asyncio.create_task(_runner())
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

        return StreamedTurn(events=channel)
```

### 6.3 `_process_stream()` — 核心，对标 session.ts L210–L320

```python
    async def _process_stream(
        self,
        input: Input,
        turn_options: TurnOptions,
        structured_output_ref: list[Any],
        on_event: Callable[[RelayEvent], None],
    ) -> None:
        prompt, images = _normalize_input(input)
        input_items = input if isinstance(input, list) else None
        translator = Translator()
        stream_state = _StreamState()
        raw_event_logger = await create_raw_event_logger(self._session_options.raw_event_log)

        fatal_cli_error: str | None = None
        stderr_text = ""

        # FailFast / 取消：合并两路信号
        cli_cancel_event = asyncio.Event() if turn_options.fail_fast_on_cli_api_error else None
        exec_cancel = _union_cancel(turn_options.cancel_event, cli_cancel_event)

        def handle_raw_event(event: RawClaudeEvent) -> None:
            nonlocal fatal_cli_error, stderr_text
            if turn_options.fail_fast_on_cli_api_error and fatal_cli_error is None:
                stderr_text = _append_stderr_text(stderr_text, event)
                err = _extract_fatal_cli_api_error(stderr_text)
                if err:
                    fatal_cli_error = err
                    if cli_cancel_event is not None:
                        cli_cancel_event.set()
            if turn_options.on_raw_event is not None:
                turn_options.on_raw_event(event)

        def handle_line(line: str) -> None:
            nonlocal fatal_cli_error
            parsed = parse_line(line)
            if parsed is None:
                return

            # 从 result 事件提取 structured_output
            if parsed.type == "result":
                so = getattr(parsed, "structured_output", None)
                if so is not None:
                    structured_output_ref[0] = so

            if turn_options.fail_fast_on_cli_api_error and fatal_cli_error is None:
                err = _extract_fatal_cli_api_error_from_stdout(parsed)
                if err:
                    fatal_cli_error = err
                    if not self._id and isinstance(parsed.session_id, str):
                        self._id = parsed.session_id
                    if cli_cancel_event is not None:
                        cli_cancel_event.set()
                    on_event(ErrorEvent(
                        message=fatal_cli_error,
                        session_id=self._id or translator.session_id,
                    ))
                    return

            relay_events = _translate_relay_events(parsed, translator, stream_state)

            if translator.session_id and not self._id:
                self._id = translator.session_id

            for ev in relay_events:
                if isinstance(ev, TurnCompleteEvent) and ev.session_id and not self._id:
                    self._id = ev.session_id
                on_event(ev)

        # 会话模式决定（对齐 session.ts L252–L253）
        if self._has_run:
            resume_id = self._id
            continue_session = False
        else:
            if self._continue_mode:
                resume_id = None
                continue_session = True
            elif self._id:
                resume_id = self._id
                continue_session = False
            else:
                resume_id = None
                continue_session = False

        try:
            await self._exec.run(ExecArgs(
                input=prompt,
                input_items=input_items,
                images=images or None,
                resume_session_id=resume_id,
                continue_session=continue_session,
                session_options=self._session_options,
                cli_path=self._global_options.cli_path,
                env=self._global_options.env,
                cancel_event=exec_cancel,
                raw_event_logger=raw_event_logger,
                on_raw_event=handle_raw_event,
                on_line=handle_line,
            ))
        except Exception:
            if fatal_cli_error is not None:
                on_event(ErrorEvent(
                    message=fatal_cli_error,
                    session_id=self._id or translator.session_id,
                ))
                return
            raise
        finally:
            await raw_event_logger.close()
            self._has_run = True
```

`_union_cancel` 把外部取消与 FailFast 内部取消合并为一个 `asyncio.Event`。

### 6.4 输入归一化 — 对齐 session.ts `normalizeInput` L323–L337

```python
def _normalize_input(input: Input) -> tuple[str, list[str]]:
    if isinstance(input, str):
        return input, []
    prompt_parts: list[str] = []
    images: list[str] = []
    for item in input:
        if item["type"] == "text":
            prompt_parts.append(item["text"])
        elif item["type"] == "local_image":
            images.append(item["path"])
    return "\n\n".join(prompt_parts), images
```

### 6.5 双层流去重 — 对齐 session.ts L339–L666

```python
@dataclass
class _StreamedMessageState:
    text_streamed: bool = False
    thinking_streamed: bool = False

@dataclass
class _StreamState:
    active_message_id: str | None = None
    last_completed_message_id: str | None = None
    messages: dict[str, _StreamedMessageState] = field(default_factory=dict)


def _translate_relay_events(
    parsed: ClaudeEvent,
    translator: Translator,
    state: _StreamState,
) -> list[RelayEvent]:
    if parsed.type == "stream_event":
        return _translate_stream_event(parsed, state)
    relay = translator.translate(parsed)
    if parsed.type == "assistant":
        return _suppress_duplicate_assistant_snapshot(parsed, relay, state)
    return relay


def _translate_stream_event(raw: ClaudeEvent, state: _StreamState) -> list[RelayEvent]:
    event = getattr(raw, "event", None) or {}
    ev_type = event.get("type")

    if ev_type == "message_start":
        msg_id = _get_message_id(event.get("message"))
        if msg_id:
            state.active_message_id = msg_id
            state.messages.setdefault(msg_id, _StreamedMessageState())
        return []

    if ev_type == "message_stop":
        msg_id = _resolve_stream_event_message_id(event, state)
        if msg_id:
            state.messages.setdefault(msg_id, _StreamedMessageState())
            state.last_completed_message_id = msg_id
            if state.active_message_id == msg_id:
                state.active_message_id = None
        return []

    if ev_type != "content_block_delta":
        return []

    delta = event.get("delta") or {}
    msg_id = _resolve_stream_event_message_id(event, state)
    msg_state = state.messages.setdefault(msg_id, _StreamedMessageState()) if msg_id else None

    if delta.get("type") == "text_delta":
        text = delta.get("text") or ""
        if not text:
            return []
        if msg_state is not None:
            msg_state.text_streamed = True
        return [TextDeltaEvent(content=text)]

    if delta.get("type") == "thinking_delta":
        thinking = delta.get("thinking") or delta.get("text") or ""
        if not thinking:
            return []
        if msg_state is not None:
            msg_state.thinking_streamed = True
        return [ThinkingDeltaEvent(content=thinking)]

    return []


def _suppress_duplicate_assistant_snapshot(
    raw: ClaudeEvent,
    relay_events: list[RelayEvent],
    state: _StreamState,
) -> list[RelayEvent]:
    msg_id = _get_message_id(raw.message) or state.last_completed_message_id
    if not msg_id:
        return relay_events
    msg_state = state.messages.get(msg_id)
    if msg_state is None:
        return relay_events
    result: list[RelayEvent] = []
    for ev in relay_events:
        if isinstance(ev, TextDeltaEvent) and msg_state.text_streamed:
            continue
        if isinstance(ev, ThinkingDeltaEvent) and msg_state.thinking_streamed:
            continue
        result.append(ev)
    return result
```

### 6.6 FailFast — 对齐 session.ts L451–L527

```python
def _append_stderr_text(buf: str, event: RawClaudeEvent) -> str:
    if isinstance(event, StderrChunkEvent):
        return (buf + event.chunk)[-16384:]
    if isinstance(event, StderrLineEvent):
        return (buf + event.line + "\n")[-16384:]
    return buf


_API_ERROR_RE = re.compile(r"\bAPI Error:", re.IGNORECASE)

def _extract_fatal_cli_api_error(stderr_text: str) -> str | None:
    m = _API_ERROR_RE.search(stderr_text)
    if not m:
        return None
    tail = stderr_text[m.start():].strip()
    if not tail:
        return None
    first_line = tail.split("\n", 1)[0].strip()
    return first_line or tail


def _extract_fatal_cli_api_error_from_stdout(parsed: ClaudeEvent) -> str | None:
    if parsed.type != "system" or parsed.subtype != "api_retry":
        return None
    error_status = getattr(parsed, "error_status", None)
    error = getattr(parsed, "error", None)
    error = error.strip() if isinstance(error, str) else None
    if error_status is None and not error:
        return None

    parts = ["API retry aborted"]
    if isinstance(error_status, int):
        parts.append(f"status {error_status}")
    if error:
        parts.append(error)
    attempt = getattr(parsed, "attempt", None)
    max_retries = getattr(parsed, "max_retries", None)
    if isinstance(attempt, int) and isinstance(max_retries, int):
        parts.append(f"attempt {attempt}/{max_retries}")
    retry_delay = getattr(parsed, "retry_delay_ms", None)
    if isinstance(retry_delay, (int, float)):
        parts.append(f"next retry in {round(retry_delay)}ms")
    return " | ".join(parts)
```

---

## 7. 原始事件日志 `_raw_event_log.py` — 对标 `raw-event-log.ts`

严格对齐：

- 默认目录 `<cwd>/agent_logs`（**不是 `logs/`**）
- 文件名：`claude-raw-events-<iso>-<pid>-<rand6>.ndjson`
- `ProcessErrorEvent` 展开为 `{name, message, stack}`（`traceback`）
- 写入结构：`{"timestamp": <ISO>, "event": <event>}`
- 只接受绝对路径；相对路径抛 `ValueError`

```python
import asyncio
import json
import os
import secrets
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

class RawEventLogger(Protocol):
    def log(self, event: RawClaudeEvent) -> None: ...
    async def close(self) -> None: ...

class _NoopLogger:
    def log(self, event: RawClaudeEvent) -> None: ...
    async def close(self) -> None: ...

class _FileLogger:
    def __init__(self, path: str) -> None:
        self._fp = open(path, "a", encoding="utf-8")
        self._closed = False

    def log(self, event: RawClaudeEvent) -> None:
        if self._closed:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": _serialize_raw_claude_event(event),
        }
        self._fp.write(json.dumps(record) + "\n")
        self._fp.flush()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fp.close()


async def create_raw_event_logger(
    option: bool | str | None,
    *,
    _now: Callable[[], datetime] | None = None,
    _rand_suffix: Callable[[], str] | None = None,
) -> RawEventLogger:
    if not option:
        return _NoopLogger()

    if isinstance(option, str):
        if not os.path.isabs(option):
            raise ValueError(
                f'rawEventLog path must be an absolute path, got: "{option}"'
            )
        directory = option
    else:
        directory = os.path.join(os.getcwd(), "agent_logs")

    await asyncio.to_thread(Path(directory).mkdir, parents=True, exist_ok=True)

    now = _now or (lambda: datetime.now(timezone.utc))
    rand_suffix = _rand_suffix or (lambda: secrets.token_hex(3))

    iso = now().strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3]
    rand = rand_suffix()   # 生产默认 6 位十六进制；测试可注入固定值
    fname = f"claude-raw-events-{iso}-{os.getpid()}-{rand}.ndjson"
    return _FileLogger(os.path.join(directory, fname))


def _serialize_raw_claude_event(event: RawClaudeEvent) -> dict[str, Any]:
    if isinstance(event, ProcessErrorEvent):
        err = event.error
        return {
            "type": "process_error",
            "error": {
                "name": type(err).__name__ if err else None,
                "message": str(err) if err else None,
                "stack": "".join(
                    traceback.format_exception(type(err), err, err.__traceback__)
                ) if err else None,
            },
        }
    return asdict(event)
```

这里额外保留 `_now` / `_rand_suffix` 两个 **测试注入缝**，不是为了改变生产行为，而是为了
让 Python 版能够等价迁移 `agent-sdk` 里“固定时间 + 固定随机后缀 → 预占文件名 → `close()`
抛错”的 raw event logger 单测；生产调用仍走默认时间源与随机后缀。

---

## 8. 测试策略

### 8.1 Fake CLI（`tests/fixtures/fake_claude.py`）

对齐 [agent-sdk/tests/unit/fixtures/fake-claude.mjs](../../agent-sdk/tests/unit/fixtures/)。Python
版脚本加 shebang `#!/usr/bin/env python3` 并在测试 fixture 中 `chmod +x`，使其能
**直接作为 cli_path** 被 `asyncio.create_subprocess_exec` 调用。

禁止在测试中使用 `cli_path=f"python3 {FAKE_CLAUDE}"` 这种 shell 风格写法（该写法在
`create_subprocess_exec` 下会 `FileNotFoundError`）。推荐两种写法：

```python
FAKE_CLAUDE = Path(__file__).parent / "fixtures" / "fake_claude.py"

# 写法 1：直接执行脚本（需要 shebang + chmod +x）
ClaudeCode(ClaudeCodeOptions(cli_path=str(FAKE_CLAUDE)))

# 写法 2：通过 sys.executable 调用（更可移植）
import sys
ClaudeCode(ClaudeCodeOptions(cli_path=sys.executable))
# 然后在 SessionOptions 里不能传，改为把 FAKE_CLAUDE 作为 args — 因此更推荐写法 1
```

### 8.2 测试先行总原则

本次实施采用 **Unit Test + E2E Test 先行**，并且测试迁移标准分两类：

- **literal-port**：CLI args 向量、artifact 文件名、raw event 字段、env 隔离、`cwd`
  语义、session 模式切换等，要求与 `agent-sdk` 逐项一致
- **semantic-port**：Python 运行时特有的异常类型、`asyncio` 取消细节、权限报错文案等，
  要求行为等价，不强求逐字符一致

额外约束：

- `tests/unit` 与 `tests/e2e` 默认 **串行执行**；当前设计下禁止 `pytest-xdist`
  或其他并行运行方式，因为若并发执行会破坏 `process.env`/`os.environ` 污染类用例
- 测试先行的“红灯”必须是**断言失败**，而不是包不可导入。因此先建立“最小可导入骨架”，
  再迁移测试
- `SessionOptions.cwd` 表示 **subprocess working directory**，不是 `--cd` flag；这点要与
  `agent-sdk/tests/unit/exec.test.ts` 的断言保持一致

### 8.3 Unit Test 1:1 迁移

Python 单测要逐条迁移 Node 版两个主文件：

- `tests/unit/test_exec.py`：1:1 对齐
  [agent-sdk/tests/unit/exec.test.ts](../../agent-sdk/tests/unit/exec.test.ts) 全部 13 条用例
- `tests/unit/test_session.py`：1:1 对齐
  [agent-sdk/tests/unit/session.test.ts](../../agent-sdk/tests/unit/session.test.ts) 全部 27 条用例
- `tests/unit/parser/*`：按 `docs/parser/design.md` 的矩阵补 parser 派生测试；这部分是
  对齐 `claude-code-parser` 行为，不是直接从 `agent-sdk` 复制

单测迁移时必须保留的硬契约：

- fake CLI 的 prompt trigger 与事件序列完全对齐 Node 版
- `build_args()` 的 flag 顺序、重复 flag 展开、`continue` / `resume` / prompt 来源优先级完全一致
- env 隔离相关测试默认污染宿主环境，验证显式 env 不泄漏
- raw event logger 要能迁移相对路径报错、默认 `agent_logs/`、backpressure、预占文件名导致
  `close()` 失败等测试场景

### 8.4 E2E 设计拆分

E2E 的目录布局、helper 契约、artifact 契约以及 14 条 `real-cli.test.ts`
逐条映射，已经拆到 [docs/e2e/design.md](./e2e/design.md)。

主文档只保留四条摘要：

- `tests/e2e` 的基础设施与 Node 版 1:1 对齐
- 无凭证时保留 `requires E2E env vars` setup-failure 用例
- 默认优先解析仓库内安装的 Claude CLI
- E2E artifacts 作为跨 SDK 对拍契约

### 8.5 Unit Test 逐条映射附录

本附录只覆盖 **direct 1:1 unit ports**：也就是直接从 `agent-sdk/tests/unit/*.ts`
迁移过来的测试。`parser` 的派生测试统一见 `docs/parser/design.md`；E2E 的详细设计与
14 条 real-cli 映射统一见 `docs/e2e/design.md`。

#### 8.5.1 `exec.test.ts` → `tests/unit/test_exec.py`

1. **Source:** `tests/unit/exec.test.ts:135` `yields NDJSON lines from the fake CLI`。**Target:** `tests/unit/test_exec.py::test_yields_ndjson_lines_from_fake_cli`。`literal-port`。要求 `ClaudeCodeExec.run()` 至少收到一行 fake CLI 输出，且每行都可 `json.loads()`，每个对象都含 `type` 字段。
2. **Source:** `tests/unit/exec.test.ts:156` `enables default streaming flags unless explicitly disabled`。**Target:** `tests/unit/test_exec.py::test_enables_default_streaming_flags_unless_explicitly_disabled`。`literal-port`。要求默认情况下传 `-p <prompt>`、`--output-format stream-json`、`--verbose`、`--include-partial-messages`，且不自动传 `--input-format`。
3. **Source:** `tests/unit/exec.test.ts:168` `omits default-on flags when verbose and partial messages are disabled`。**Target:** `tests/unit/test_exec.py::test_omits_default_on_flags_when_verbose_and_partial_messages_are_disabled`。`literal-port`。要求当 `verbose=False` 且 `include_partial_messages=False` 时，这两个默认开启的 flag 都不出现。
4. **Source:** `tests/unit/exec.test.ts:180` `applies precedence for continue, permission mode, and system prompt source`。**Target:** `tests/unit/test_exec.py::test_applies_precedence_for_continue_permission_mode_and_system_prompt_source`。`literal-port`。要求 `continue_session=True` 时压过 `resumeSessionId`，`systemPrompt` 压过 `systemPromptFile`，`dangerously_skip_permissions=True` 压过 `permission_mode`。
5. **Source:** `tests/unit/exec.test.ts:204` `expands repeated flags for list-style options and uses stream-json stdin for images`。**Target:** `tests/unit/test_exec.py::test_expands_repeated_flags_for_list_style_options_and_uses_stream_json_stdin_for_images`。`literal-port`。要求 `additionalDirectories`、`allowedTools`、`disallowedTools`、`mcpConfig`、`pluginDir` 都展开为重复 flag；输入里有图片时切到 `--input-format stream-json`，并通过 stdin 发送 prompt + image blocks。
6. **Source:** `tests/unit/exec.test.ts:249` `passes scalar flags through and serializes object agents`。**Target:** `tests/unit/test_exec.py::test_passes_scalar_flags_through_and_serializes_object_agents`。`literal-port`。要求所有标量选项透传到正确 flag；`cwd` 仅作为 subprocess working directory 使用，不生成 `--cd`；`agents` 的 object 形式要 JSON 序列化后传给 `--agents`。
7. **Source:** `tests/unit/exec.test.ts:353` `supports chrome, debug, and agents string forms`。**Target:** `tests/unit/test_exec.py::test_supports_chrome_debug_and_agents_string_forms`。`literal-port`。要求 `chrome=True` 走 `--chrome`，`debug=True` 走无参数 `--debug`，`agents` 为原始字符串时原样透传，不再二次编码。
8. **Source:** `tests/unit/exec.test.ts:369` `passes --resume when resumeSessionId is set`。**Target:** `tests/unit/test_exec.py::test_passes_resume_when_resume_session_id_is_set`。`literal-port`。要求 `resume_session_id` 被传给 fake CLI 后，fake CLI 返回的 `session_id` 等于该值。
9. **Source:** `tests/unit/exec.test.ts:389` `emits raw process events including stdout and stderr chunks/lines`。**Target:** `tests/unit/test_exec.py::test_emits_raw_process_events_including_stdout_and_stderr_chunks_and_lines`。`literal-port`。要求 raw event 流里依次能观察到 `spawn`、`stdin_closed`、`stdout_line`、`stderr_chunk`、`stderr_line`、`exit`，并验证关键字段值。
10. **Source:** `tests/unit/exec.test.ts:447` `uses explicit env override without inheriting process.env`。**Target:** `tests/unit/test_exec.py::test_uses_explicit_env_override_without_inheriting_os_environ`。`literal-port`。要求显式构造函数 env 只携带指定值，不继承宿主 `os.environ` 的脏值，尤其是 `ANTHROPIC_*`。
11. **Source:** `tests/unit/exec.test.ts:472` `allows per-run env to override constructor env`。**Target:** `tests/unit/test_exec.py::test_allows_per_run_env_to_override_constructor_env`。`literal-port`。要求单次运行传入的 env 覆盖构造函数 env 中同名键。
12. **Source:** `tests/unit/exec.test.ts:493` `does not inherit global env when no explicit env is provided`。**Target:** `tests/unit/test_exec.py::test_does_not_inherit_global_env_when_no_explicit_env_is_provided`。`literal-port`。要求当没有显式 env 时，CLI 进程里也看不到宿主环境中的 `ANTHROPIC_*` 或测试污染变量。
13. **Source:** `tests/unit/exec.test.ts:509` `merges constructor env with per-run env without credential mutual exclusion`。**Target:** `tests/unit/test_exec.py::test_merges_constructor_env_with_per_run_env_without_credential_mutual_exclusion`。`literal-port`。要求 per-run env 只覆盖同名键，未覆盖的构造函数 env 值要继续保留，且 `api_key` / `auth_token` / `base_url` 不做互斥清空。

#### 8.5.2 `session.test.ts` → `tests/unit/test_session.py`

1. **Source:** `tests/unit/session.test.ts:105` `returns a complete Turn with finalResponse and usage`。**Target:** `tests/unit/test_session.py::test_returns_a_complete_turn_with_final_response_and_usage`。`literal-port`。要求 `Session.run()` 返回完整 `Turn`，包含 `final_response`、`events`、`session_id` 与 `usage` 聚合字段。
2. **Source:** `tests/unit/session.test.ts:122` `captures session ID from session_meta`。**Target:** `tests/unit/test_session.py::test_captures_session_id_from_session_meta`。`literal-port`。要求 `session.id` 在收到 `session_meta` 后被更新。
3. **Source:** `tests/unit/session.test.ts:133` `throws on error response`。**Target:** `tests/unit/test_session.py::test_throws_on_error_response`。`semantic-port`。要求 result/error 最终转成抛错，并包含 fake CLI 返回的错误文本。
4. **Source:** `tests/unit/session.test.ts:142` `can fail fast on fatal CLI API errors written to stderr`。**Target:** `tests/unit/test_session.py::test_can_fail_fast_on_fatal_cli_api_errors_written_to_stderr`。`literal-port`。要求开启 `fail_fast_on_cli_api_error` 后，stderr 命中 `API Error:` 时快速失败，而不是等待子进程自然退出。
5. **Source:** `tests/unit/session.test.ts:159` `can fail fast on fatal CLI api_retry events written to stdout`。**Target:** `tests/unit/test_session.py::test_can_fail_fast_on_fatal_cli_api_retry_events_written_to_stdout`。`literal-port`。要求 stdout 里的 `system.api_retry` 事件同样触发快速失败，并携带 `authentication_failed` 等错误信息。
6. **Source:** `tests/unit/session.test.ts:176` `supports multi-turn via automatic --resume`。**Target:** `tests/unit/test_session.py::test_supports_multi_turn_via_automatic_resume`。`literal-port`。要求同一 `Session` 首轮不带 `--resume`，第二轮自动带上前一轮拿到的 `session_id`。
7. **Source:** `tests/unit/session.test.ts:200` `yields RelayEvents as AsyncGenerator`。**Target:** `tests/unit/test_session.py::test_yields_relay_events_as_async_generator`。`literal-port`。要求 `run_streamed()` 返回异步可迭代事件流，并至少包含 `session_meta` 与 `turn_complete`。
8. **Source:** `tests/unit/session.test.ts:221` `streams text_delta events incrementally`。**Target:** `tests/unit/test_session.py::test_streams_text_delta_events_incrementally`。`literal-port`。要求 `text_delta` 被拆成两段增量 `"Here is "` 与 `"my response."`，并能重新拼回完整文本。
9. **Source:** `tests/unit/session.test.ts:240` `streams tool_use and tool_result events`。**Target:** `tests/unit/test_session.py::test_streams_tool_use_and_tool_result_events`。`literal-port`。要求 streaming 模式里同时看到 `tool_use` 与 `tool_result` 两种 relay event。
10. **Source:** `tests/unit/session.test.ts:259` `can surface fatal CLI API stderr as a RelayEvent error`。**Target:** `tests/unit/test_session.py::test_can_surface_fatal_cli_api_stderr_as_a_relay_event_error`。`literal-port`。要求在 streaming 模式下，stderr fail-fast 最终以 `error` relay event 形式暴露给调用方，而不是直接吞掉。
11. **Source:** `tests/unit/session.test.ts:287` `can surface fatal CLI api_retry stdout events as a RelayEvent error`。**Target:** `tests/unit/test_session.py::test_can_surface_fatal_cli_api_retry_stdout_events_as_a_relay_event_error`。`literal-port`。要求 stdout `api_retry` fail-fast 也生成 `error` relay event，消息中保留 `status 401` 与 `authentication_failed`。
12. **Source:** `tests/unit/session.test.ts:317` `resumes with given session ID`。**Target:** `tests/unit/test_session.py::test_resumes_with_given_session_id`。`literal-port`。要求 `resume_session("id")` 的首轮直接使用该 `session_id`，无需先执行一次普通 run。
13. **Source:** `tests/unit/session.test.ts:331` `uses --continue flag`。**Target:** `tests/unit/test_session.py::test_uses_continue_flag`。`literal-port`。要求 `continue_session()` 走 `--continue` 路径，并能正常返回结果。
14. **Source:** `tests/unit/session.test.ts:345` `accepts UserInput array with text`。**Target:** `tests/unit/test_session.py::test_accepts_user_input_array_with_text`。`literal-port`。要求多段 text input 会被归一化后正常发送，最终 run 可成功完成。
15. **Source:** `tests/unit/session.test.ts:359` `sends local_image items through stream-json stdin instead of --image`。**Target:** `tests/unit/test_session.py::test_sends_local_image_items_through_stream_json_stdin_instead_of_image_flag`。`literal-port`。要求 `local_image` 输入走 stdin `stream-json`，`args` 中出现 `--input-format` 而不出现 `--image`。
16. **Source:** `tests/unit/session.test.ts:404` `aborts a running session`。**Target:** `tests/unit/test_session.py::test_aborts_a_running_session`。`semantic-port`。要求外部取消信号能中断慢请求，并把异常向上传播。
17. **Source:** `tests/unit/session.test.ts:423` `passes only explicit env from ClaudeCodeOptions into the CLI process`。**Target:** `tests/unit/test_session.py::test_passes_only_explicit_env_from_claude_code_options_into_the_cli_process`。`literal-port`。要求 `ClaudeCodeOptions` 里的 `api_key` / `auth_token` / `base_url` 会显式进入 CLI env，且不掺入宿主其他变量。
18. **Source:** `tests/unit/session.test.ts:461` `forwards TurnOptions.onRawEvent through runStreamed`。**Target:** `tests/unit/test_session.py::test_forwards_turn_options_on_raw_event_through_run_streamed`。`literal-port`。要求 `run_streamed(..., on_raw_event=...)` 能把 raw event 一路转发给调用方。
19. **Source:** `tests/unit/session.test.ts:482` `writes raw event logs as NDJSON when enabled`。**Target:** `tests/unit/test_session.py::test_writes_raw_event_logs_as_ndjson_when_enabled`。`literal-port`。要求启用 `raw_event_log` 后，目录里生成一份 NDJSON 文件，记录 `spawn/stdout_line/stderr_chunk/stderr_line/exit` 等事件。
20. **Source:** `tests/unit/session.test.ts:529` `rejects a pending streamed iterator when processing fails`。**Target:** `tests/unit/test_session.py::test_rejects_a_pending_streamed_iterator_when_processing_fails`。`semantic-port`。要求底层执行器延迟报错时，已经暴露出去的异步迭代器在 `next()` 时收到异常，而不是无穷等待。
21. **Source:** `tests/unit/session.test.ts:542` `merges abort signals without a reason and cleans up listeners`。**Target:** `tests/unit/test_session.py::test_merges_abort_signals_without_a_reason_and_cleans_up_listeners`。`semantic-port`。要求外部取消信号与内部 fail-fast 信号合并后仍能正确清理监听器。
22. **Source:** `tests/unit/session.test.ts:564` `preserves abort reasons when merging abort signals`。**Target:** `tests/unit/test_session.py::test_preserves_abort_reasons_when_merging_abort_signals`。`semantic-port`。要求合并后的取消信号保留原始 reason，不要在传播过程中丢失。
23. **Source:** `tests/unit/session.test.ts:586` `rejects relative rawEventLog paths`。**Target:** `tests/unit/test_session.py::test_rejects_relative_raw_event_log_paths`。`literal-port`。要求 `raw_event_log="relative/path"` 直接报错，且错误文本说明必须使用绝对路径。
24. **Source:** `tests/unit/session.test.ts:592` `uses the default agent_logs directory and serializes process errors`。**Target:** `tests/unit/test_session.py::test_uses_the_default_agent_logs_directory_and_serializes_process_errors`。`literal-port`。要求 `raw_event_log=True` 默认写入 `<cwd>/agent_logs`，并把 `process_error` 序列化成 `{name,message,stack}`。
25. **Source:** `tests/unit/session.test.ts:635` `waits for drain before closing after a backpressured write`。**Target:** `tests/unit/test_session.py::test_waits_for_drain_before_closing_after_a_backpressured_write`。`literal-port`。要求 logger 在大块写入触发 backpressure 时先 `drain` 再 `close()`，最终文件内容完整落盘。
26. **Source:** `tests/unit/session.test.ts:657` `rethrows fatal stream errors captured before close completes`。**Target:** `tests/unit/test_session.py::test_rethrows_fatal_stream_errors_captured_before_close_completes`。`literal-port`。要求通过固定时间源与随机后缀复现目标文件名冲突，验证 `close()` 会重新抛出底层 fatal stream error。
27. **Source:** `tests/unit/session.test.ts:699` `throws close errors when the underlying stream cannot open`。**Target:** `tests/unit/test_session.py::test_throws_close_errors_when_the_underlying_stream_cannot_open`。`semantic-port`。要求底层流因为权限或目录状态无法打开时，`close()` 不能静默成功，必须把错误抛出。

E2E 的 14 条 `real-cli.test.ts` direct port 映射已迁到 [docs/e2e/design.md](./e2e/design.md)。

---

## 9. 打包配置

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claude-code-python"
version = "0.0.1"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "mypy>=1.9", "ruff>=0.4"]

[tool.hatch.build.targets.wheel]
packages = ["src/claude_code"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 10. 实施顺序

| Phase | 文件/产物 | 目标 |
|-------|-----------|------|
| 1 | `tests/fixtures/fake_claude.py` + 最小可导入骨架 | 让 pytest 能加载并运行测试，而不是先报 ImportError |
| 2 | `tests/unit/test_exec.py` + `tests/unit/test_session.py` | 1:1 迁移 `agent-sdk` 40 条 unit tests |
| 3 | `tests/e2e/*` + `tests/e2e/fixtures/images/*` + `docs/e2e/design.md` | 1:1 迁移 E2E harness / config / reporters / 14 条真实 CLI 用例，并按独立 E2E 设计文档落地 |
| 4 | `parser/` 子包（详见 `docs/parser/design.md`） | 先让 parser 派生测试变绿 |
| 5 | `_options.py` + `_raw_event_log.py` | 类型层与 logger 测试变绿 |
| 6 | `_exec.py` | 让 exec 单测变绿 |
| 7 | `_session.py` | 让 session 单测变绿 |
| 8 | `_client.py` + `__init__.py` re-export | 打通公开 API 与 E2E |

---

## 11. 与 agent-sdk 的对齐矩阵

### 11.1 类型字段

| 类型 | 对齐状态 | 源文件 |
|------|---------|--------|
| `ClaudeCodeOptions`（5 字段）| ✅ 完全一致 | `options.ts` L4–L15 |
| `SessionOptions`（35+ 字段）| ✅ 字段/命名/联合类型一致 | `options.ts` L56–L142 |
| `TurnOptions`（cancel_event/on_raw_event/fail_fast_on_cli_api_error）| ✅ 一致 | `options.ts` L145–L155 |
| `RawClaudeEvent`（7 变体：spawn/stdin_closed/stdout_line/stderr_chunk/stderr_line/process_error/exit）| ✅ 一致（无 stdout_chunk）| `options.ts` L29–L37 |
| `AgentDefinition` | ✅ 字段一致 | `options.ts` L40–L53 |
| `Turn`（events/final_response/usage/session_id/structured_output）| ✅ 字段一致 | `session.ts` L34–L40 |
| `TurnUsage` | ✅ 一致 | `session.ts` L27–L32 |
| `Input` / `UserInput` | ✅ 一致 | `session.ts` L19–L23 |

### 11.2 CLI 参数映射

与 [agent-sdk/src/exec.ts](../../agent-sdk/src/exec.ts) `buildArgs()` L194–L419 顺序完全一致：
`-p / --input-format / --output-format / --verbose / --include-partial-messages /
--continue|--resume / --session-id / --fork-session / --model / --add-dir... /
--max-turns / --max-budget-usd / --system-prompt(|-file) / --append-system-prompt(|-file) /
--dangerously-skip-permissions|--permission-mode / --allowedTools... / --disallowedTools... /
--tools / --permission-prompt-tool / --mcp-config... / --strict-mcp-config / --effort /
--fallback-model / --bare / --no-session-persistence / --chrome|--no-chrome / --agents /
--agent / --name / --settings / --setting-sources <默认""> / --include-hook-events /
--betas / --worktree / --disable-slash-commands / --plugin-dir... /
--exclude-dynamic-system-prompt-sections / --debug(|<str>) / --debug-file / --json-schema`。

### 11.3 行为对齐

| 行为 | 对齐点 | 源 |
|------|--------|----|
| `mergeClaudeEnv`（apiKey/authToken/baseUrl → 环境变量） | ✅ `_merge_claude_env` | `claude-code.ts` L50–L68 |
| 首轮 resume 语义（`_has_run` 状态机） | ✅ 完全一致 | `session.ts` L252–L253 + L318 |
| `run()` 回填 `structured_output`（通过引用列表） | ✅ 修正了原设计的 bug | `session.ts` L156 + L187 |
| 双层流去重（stream_event vs assistant snapshot） | ✅ 完全复刻 `StreamState` | `session.ts` L339–L666 |
| FailFast 两路（stderr `\bAPI Error:` + stdout `system.api_retry`） | ✅ 完全一致 | `session.ts` L451–L527 |
| `setting-sources` 默认传 `""` | ✅ 处理 | `exec.ts` L361–L362 |
| stdin 图片走 stream-json（支持 `inputItems` 与 `images` 两路） | ✅ 对齐 `getStructuredInputItems` | `exec.ts` L421–L507 |
| `detectImageMediaType` 顺序 PNG/JPEG/GIF/WEBP/扩展名回退 | ✅ 一致 | `exec.ts` L509–L564 |
| raw log 默认 `agent_logs/`、文件名 `claude-raw-events-<iso>-<pid>-<rand6>`、process_error 展开 | ✅ 一致 | `raw-event-log.ts` L40–L123 |
| raw log 测试缝（固定时间/随机后缀复现 close error） | ✅ 通过 `_now` / `_rand_suffix` 注入等价迁移 | `tests/unit/session.test.ts` L657–L699 |
| 绝对路径强制（相对路径抛错） | ✅ 一致 | `raw-event-log.ts` L33–L38 |
| stderr 既发 `stderr_chunk` 又发 `stderr_line` | ✅ 一致 | `exec.ts` L118–L137 |
| spawn 失败先 `process_error` 再抛错 | ✅ 一致 | `exec.ts` L92–L95 |
| 进程清理（`terminate` → `kill` → `wait`）| ✅ 防僵尸 | `exec.ts` L181–L190 |
| 测试先行顺序（fixture → 测试迁移 → 实现） | ✅ 明确为 TDD-first | `tests/unit/*` + `tests/e2e/*` |

### 11.4 公开 API 导出边界

与 [agent-sdk/src/index.ts](../../agent-sdk/src/index.ts) 对齐：SDK 层导出
`ClaudeCode / Session + 所有 Options / Input / Turn / ...`；parser 类型与工具通过
`from claude_code.parser import ...` **re-export**，不在 SDK 层重复定义。
