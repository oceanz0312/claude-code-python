from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, TypedDict, Union


PermissionMode = Literal[
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass
class ClaudeCodeOptions:
    cli_path: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    base_url: Optional[str] = None


@dataclass
class AgentDefinition:
    description: Optional[str] = None
    prompt: Optional[str] = None
    tools: Optional[List[str]] = None
    allowed_tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    effort: Optional[Effort] = None
    max_turns: Optional[int] = None
    permission_mode: Optional[PermissionMode] = None
    isolation: Optional[Literal["worktree"]] = None
    initial_prompt: Optional[str] = None
    mcp_servers: Optional[Dict[str, Any]] = None


@dataclass
class SessionOptions:
    model: Optional[str] = None
    cwd: Optional[str] = None
    additional_directories: Optional[List[str]] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    system_prompt: Optional[str] = None
    system_prompt_file: Optional[str] = None
    append_system_prompt: Optional[str] = None
    append_system_prompt_file: Optional[str] = None
    permission_mode: Optional[PermissionMode] = None
    dangerously_skip_permissions: Optional[bool] = None
    allowed_tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    tools: Optional[str] = None
    permission_prompt_tool: Optional[str] = None
    mcp_config: Optional[Union[str, List[str]]] = None
    strict_mcp_config: Optional[bool] = None
    effort: Optional[Effort] = None
    fallback_model: Optional[str] = None
    bare: Optional[bool] = None
    no_session_persistence: Optional[bool] = None
    chrome: Optional[bool] = None
    agents: Optional[Union[Dict[str, AgentDefinition], str]] = None
    agent: Optional[str] = None
    name: Optional[str] = None
    settings: Optional[str] = None
    setting_sources: Optional[str] = None
    verbose: Optional[bool] = None
    include_partial_messages: Optional[bool] = None
    include_hook_events: Optional[bool] = None
    betas: Optional[str] = None
    worktree: Optional[str] = None
    disable_slash_commands: Optional[bool] = None
    plugin_dir: Optional[Union[str, List[str]]] = None
    exclude_dynamic_system_prompt_sections: Optional[bool] = None
    debug: Optional[Union[str, bool]] = None
    debug_file: Optional[str] = None
    json_schema: Optional[Union[str, Dict[str, Any]]] = None
    session_id: Optional[str] = None
    fork_session: Optional[bool] = None
    raw_event_log: Optional[Union[bool, str]] = None


@dataclass
class SpawnEvent:
    command: str = ""
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    type: str = "spawn"


@dataclass
class StdinClosedEvent:
    type: str = "stdin_closed"


@dataclass
class StdoutLineEvent:
    line: str = ""
    type: str = "stdout_line"


@dataclass
class StderrChunkEvent:
    chunk: str = ""
    type: str = "stderr_chunk"


@dataclass
class StderrLineEvent:
    line: str = ""
    type: str = "stderr_line"


@dataclass
class ProcessErrorEvent:
    error: Optional[BaseException] = None
    type: str = "process_error"


@dataclass
class ExitEvent:
    code: Optional[int] = None
    signal: Optional[str] = None
    type: str = "exit"


RawClaudeEvent = Union[
    SpawnEvent,
    StdinClosedEvent,
    StdoutLineEvent,
    StderrChunkEvent,
    StderrLineEvent,
    ProcessErrorEvent,
    ExitEvent,
]


@dataclass
class TurnOptions:
    signal: Any = None
    on_raw_event: Optional[Callable[[RawClaudeEvent], None]] = None
    fail_fast_on_cli_api_error: bool = False


class UserTextInput(TypedDict):
    type: Literal["text"]
    text: str


class UserLocalImageInput(TypedDict):
    type: Literal["local_image"]
    path: str


UserInput = Union[UserTextInput, UserLocalImageInput]
