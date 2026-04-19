from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ModelUsageEntry:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    context_window: int = 0


@dataclass
class ClaudeContent:
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
    content: List[ClaudeContent] = field(default_factory=list)
    role: Optional[str] = None
    stop_reason: Optional[str] = None
    id: Optional[str] = None


@dataclass
class ClaudeEvent:
    type: str
    subtype: Optional[str] = None
    message: Optional[ClaudeMessage] = None
    result: Any = None
    session_id: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[List[str]] = None
    duration_ms: Optional[int] = None
    duration_api_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    total_cost_usd: Optional[float] = None
    is_error: Optional[bool] = None
    num_turns: Optional[int] = None
    model_usage: Optional[Dict[str, ModelUsageEntry]] = None
    usage: Any = None
    event: Any = None
    attempt: Optional[int] = None
    max_retries: Optional[int] = None
    retry_delay_ms: Optional[int] = None
    error_status: Optional[int] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def claude_event_from_dict(data: Dict[str, Any]) -> ClaudeEvent:
    message = None
    message_data = data.get("message")
    if isinstance(message_data, dict):
        content_items = []
        for item in message_data.get("content", []) or []:
            if isinstance(item, dict):
                content_items.append(
                    ClaudeContent(
                        type=str(item.get("type", "")),
                        text=item.get("text"),
                        thinking=item.get("thinking"),
                        id=item.get("id"),
                        name=item.get("name"),
                        input=item.get("input"),
                        content=item.get("content"),
                        tool_use_id=item.get("tool_use_id"),
                        is_error=item.get("is_error"),
                    )
                )
        message = ClaudeMessage(
            content=content_items,
            role=message_data.get("role"),
            stop_reason=message_data.get("stop_reason"),
            id=message_data.get("id"),
        )

    model_usage = None
    raw_model_usage = data.get("modelUsage")
    if isinstance(raw_model_usage, dict):
        model_usage = {}
        for model_id, entry in raw_model_usage.items():
            if isinstance(entry, dict):
                model_usage[model_id] = ModelUsageEntry(
                    input_tokens=int(entry.get("inputTokens", 0) or 0),
                    output_tokens=int(entry.get("outputTokens", 0) or 0),
                    cache_read_input_tokens=int(entry.get("cacheReadInputTokens", 0) or 0),
                    cache_creation_input_tokens=int(entry.get("cacheCreationInputTokens", 0) or 0),
                    context_window=int(entry.get("contextWindow", 0) or 0),
                )

    return ClaudeEvent(
        type=str(data.get("type", "")),
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
