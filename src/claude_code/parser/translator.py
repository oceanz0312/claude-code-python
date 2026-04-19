from __future__ import annotations

import json

from .events import (
    ErrorEvent,
    SessionMetaEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
    TurnCompleteEvent,
)


class Translator:
    def __init__(self) -> None:
        self._last_content_index = 0
        self._last_first_block_key = None
        self._session_id = None
        self._model = None

    @property
    def session_id(self):
        return self._session_id

    @property
    def model(self):
        return self._model

    def reset(self) -> None:
        self._last_content_index = 0
        self._last_first_block_key = None

    def translate(self, raw):
        if raw.type == "system":
            return self._translate_system(raw)
        if raw.type == "result":
            return self._translate_result(raw)
        if raw.type == "assistant":
            return self._translate_assistant(raw)
        if raw.type == "user":
            return self._translate_user(raw)
        return []

    def _translate_system(self, raw):
        if raw.subtype == "init":
            if raw.session_id:
                self._session_id = raw.session_id
            if raw.model:
                self._model = raw.model
            return [SessionMetaEvent(model=raw.model or "unknown")]

        if raw.subtype == "result":
            result_text = _parse_double_encoded_result(raw.result)
            self.reset()
            if raw.is_error:
                return [ErrorEvent(message=result_text, session_id=raw.session_id)]
            return [TurnCompleteEvent(session_id=raw.session_id)]

        return []

    def _translate_result(self, raw):
        result_text = _parse_double_encoded_result(raw.result)
        if raw.subtype == "error" or raw.is_error:
            self.reset()
            return [ErrorEvent(message=result_text, session_id=raw.session_id)]

        event = TurnCompleteEvent(
            session_id=raw.session_id,
            cost_usd=raw.total_cost_usd,
        )
        if raw.model_usage:
            for usage in raw.model_usage.values():
                event.input_tokens = (
                    usage.input_tokens
                    + usage.cache_read_input_tokens
                    + usage.cache_creation_input_tokens
                )
                event.output_tokens = usage.output_tokens
                event.context_window = usage.context_window
                break

        self.reset()
        return [event]

    def _translate_assistant(self, raw):
        message = raw.message
        if not message or not message.content:
            return []

        first_key = _block_fingerprint(message.content[0])
        if first_key != self._last_first_block_key:
            self._last_content_index = 0
            self._last_first_block_key = first_key

        if len(message.content) < self._last_content_index:
            self._last_content_index = 0

        events = []
        for block in message.content[self._last_content_index :]:
            event = self._translate_content_block(block)
            if event is not None:
                events.append(event)

        self._last_content_index = len(message.content)
        return events

    def _translate_user(self, raw):
        message = raw.message
        if not message or not message.content:
            return []

        events = []
        for block in message.content:
            if block.type == "tool_result":
                events.append(
                    ToolResultEvent(
                        tool_use_id=block.tool_use_id or "",
                        output=extract_content(block.content),
                        is_error=bool(block.is_error),
                    )
                )
        return events

    def _translate_content_block(self, block):
        if block.type == "text":
            return TextDeltaEvent(content=block.text or "")

        if block.type == "thinking":
            content = block.thinking or block.text or ""
            if not content:
                return None
            return ThinkingDeltaEvent(content=content)

        if block.type == "tool_use":
            return ToolUseEvent(
                tool_use_id=block.id or "",
                tool_name=block.name or "",
                input=json.dumps(block.input) if block.input is not None else "",
            )

        if block.type == "tool_result":
            return ToolResultEvent(
                tool_use_id=block.tool_use_id or "",
                output=extract_content(block.content),
                is_error=bool(block.is_error),
            )

        return None


def extract_content(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(raw)


def _parse_double_encoded_result(result):
    if result is None:
        return ""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, str):
                return parsed
        except Exception:
            pass
        return result
    return str(result)


def _block_fingerprint(block):
    if block.id:
        return "%s:%s" % (block.type, block.id)

    text = block.thinking or block.text or ""
    if text:
        return "%s:%s" % (block.type, text[:64])

    return "%s:%s" % (block.type, block.tool_use_id or "unknown")
