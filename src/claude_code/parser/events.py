from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class TextDeltaEvent:
    content: str = ""
    type: str = "text_delta"


@dataclass
class ThinkingDeltaEvent:
    content: str = ""
    type: str = "thinking_delta"


@dataclass
class ToolUseEvent:
    tool_use_id: str = ""
    tool_name: str = ""
    input: str = ""
    type: str = "tool_use"


@dataclass
class ToolResultEvent:
    tool_use_id: str = ""
    output: str = ""
    is_error: bool = False
    type: str = "tool_result"


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


RelayEvent = Union[
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolUseEvent,
    ToolResultEvent,
    SessionMetaEvent,
    TurnCompleteEvent,
    ErrorEvent,
]
