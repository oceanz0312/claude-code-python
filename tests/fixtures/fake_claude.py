#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any


ARGS = sys.argv[1:]


def _get_arg_value(flag: str) -> str | None:
    try:
        index = ARGS.index(flag)
    except ValueError:
        return None
    return ARGS[index + 1] if index + 1 < len(ARGS) else None


INPUT_FORMAT = _get_arg_value("--input-format")


class SigintExit(SystemExit):
    pass


def _handle_sigint(_signum: int, _frame: Any) -> None:
    raise SigintExit(130)


signal.signal(signal.SIGINT, _handle_sigint)


def _get_prompt_arg() -> str:
    try:
        index = ARGS.index("-p")
    except ValueError:
        return ""
    if index + 1 >= len(ARGS):
        return ""
    next_arg = ARGS[index + 1]
    if next_arg.startswith("-"):
        return ""
    return next_arg


def _read_stdin_text() -> str:
    return sys.stdin.read()


def _read_input() -> tuple[str, int]:
    stdin_text = _read_stdin_text()
    if INPUT_FORMAT != "stream-json":
        return _get_prompt_arg(), 0

    prompt_parts: list[str] = []
    image_count = 0

    for line in stdin_text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            parsed = json.loads(trimmed)
        except Exception:
            continue

        content = (((parsed or {}).get("message") or {}).get("content"))
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                prompt_parts.append(block["text"])
            elif block.get("type") == "image":
                image_count += 1

    return "\n\n".join(prompt_parts), image_count


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _emit_assistant_thinking(text: str, sid: str) -> None:
    _emit(
        {
            "type": "assistant",
            "session_id": sid,
            "message": {"content": [{"type": "thinking", "thinking": text}]},
        }
    )


def _emit_message_start(message_id: str, sid: str) -> None:
    _emit(
        {
            "type": "stream_event",
            "session_id": sid,
            "event": {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                },
            },
        }
    )
    _emit(
        {
            "type": "stream_event",
            "session_id": sid,
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        }
    )


def _emit_text_delta(message_id: str, text: str, sid: str) -> None:
    _emit(
        {
            "type": "stream_event",
            "session_id": sid,
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
                "message_id": message_id,
            },
        }
    )


def _emit_tool_use(tool_id: str, name: str, input_payload: dict[str, Any], sid: str) -> None:
    _emit(
        {
            "type": "assistant",
            "session_id": sid,
            "message": {
                "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": input_payload}]
            },
        }
    )


def _emit_tool_result(tool_use_id: str, is_error: bool, content: str, sid: str) -> None:
    _emit(
        {
            "type": "user",
            "session_id": sid,
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error, "content": content}]
            },
        }
    )


def _emit_assistant_text(text: str, sid: str, message_id: str) -> None:
    _emit(
        {
            "type": "assistant",
            "session_id": sid,
            "message": {"id": message_id, "content": [{"type": "text", "text": text}]},
        }
    )
    _emit(
        {
            "type": "stream_event",
            "session_id": sid,
            "event": {"type": "message_stop", "message_id": message_id},
        }
    )


def _emit_result(text: str, sid: str) -> None:
    _emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": sid,
            "total_cost_usd": 0.001,
            "duration_ms": 500,
            "duration_api_ms": 300,
            "num_turns": 1,
            "modelUsage": {
                "claude-sonnet-4-20250514": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "contextWindow": 200000,
                }
            },
        }
    )


def _delay(ms: int) -> None:
    time.sleep(ms / 1000)


def main() -> int:
    prompt, image_count = _read_input()
    session_id = _get_arg_value("--resume") or _get_arg_value("--session-id") or "test-session-001"
    inspect_payload = {
        "args": ARGS,
        "cwd": os.getcwd(),
        "flags": {
            "resumeSessionId": _get_arg_value("--resume"),
            "continueSession": "--continue" in ARGS,
        },
        "input": {
            "prompt": prompt,
            "imageCount": image_count,
            "inputFormat": INPUT_FORMAT,
        },
        "env": {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
            "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
            "INSPECT_CUSTOM_ENV": os.environ.get("INSPECT_CUSTOM_ENV"),
            "INSPECT_INHERITED_ENV": os.environ.get("INSPECT_INHERITED_ENV"),
        },
    }

    if "__inspect_exec_options__" in prompt:
        _emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "inspect-exec-options",
                "session_id": session_id,
                "inspection": inspect_payload,
                "total_cost_usd": 0,
                "duration_ms": 1,
                "duration_api_ms": 1,
                "num_turns": 1,
                "modelUsage": {},
            }
        )
        return 0

    if "__inspect_session_flags__" in prompt:
        report = json.dumps(inspect_payload["flags"])
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": "claude-sonnet-4-20250514",
                "tools": ["Read"],
            }
        )
        _emit_message_start("msg_inspect", session_id)
        _emit_text_delta("msg_inspect", report, session_id)
        _emit_assistant_text(report, session_id, "msg_inspect")
        _emit_result(report, session_id)
        return 0

    if "__inspect_raw_events__" in prompt:
        sys.stderr.write("raw stderr line\n")
        sys.stderr.flush()
        _emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "inspect-raw-events",
                "session_id": session_id,
                "total_cost_usd": 0,
                "duration_ms": 1,
                "duration_api_ms": 1,
                "num_turns": 1,
                "modelUsage": {},
            }
        )
        return 0

    if "__stderr_api_error__" in prompt:
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": "claude-sonnet-4-20250514",
                "tools": ["Read"],
            }
        )
        sys.stderr.write('API Error: 502 {"error":{"message":"proxy failed","type":"proxy_error"}}')
        sys.stderr.flush()
        _delay(1500)
        return 1

    if "__stdout_api_retry_auth__" in prompt:
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": "claude-sonnet-4-20250514",
                "tools": ["Read"],
            }
        )
        _emit(
            {
                "type": "system",
                "subtype": "api_retry",
                "attempt": 1,
                "max_retries": 10,
                "retry_delay_ms": 600,
                "error_status": 401,
                "error": "authentication_failed",
                "session_id": session_id,
            }
        )
        _delay(1500)
        return 1

    if "force-error" in prompt:
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": "claude-sonnet-4-20250514",
                "tools": ["Read", "Write", "Bash"],
            }
        )
        _emit(
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "Something went wrong",
                "session_id": session_id,
            }
        )
        return 0

    if "slow-run" in prompt:
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": "claude-sonnet-4-20250514",
                "tools": ["Read"],
            }
        )
        _emit_assistant_thinking("Preparing slow run...", session_id)
        _emit_message_start("msg_slow", session_id)
        _emit_text_delta("msg_slow", "Still working", session_id)
        _delay(5000)
        _emit_result("slow run done", session_id)
        return 0

    _emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "model": "claude-sonnet-4-20250514",
            "tools": ["Read", "Write", "Bash", "Edit", "Glob", "Grep"],
        }
    )
    _emit_assistant_thinking("Let me analyze this...", session_id)
    _emit_message_start("msg_main", session_id)
    _emit_text_delta("msg_main", "Here is ", session_id)
    _emit_text_delta("msg_main", "my response.", session_id)
    _emit_tool_use("tool_1", "Read", {"file_path": "/tmp/test.txt"}, session_id)
    _emit_tool_result("tool_1", False, "file contents here", session_id)
    _emit_assistant_text("Here is my response.", session_id, "msg_main")
    _emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Here is my response.",
            "session_id": session_id,
            "total_cost_usd": 0.003,
            "duration_ms": 1200,
            "duration_api_ms": 800,
            "num_turns": 1,
            "modelUsage": {
                "claude-sonnet-4-20250514": {
                    "inputTokens": 500,
                    "outputTokens": 100,
                    "cacheReadInputTokens": 50,
                    "cacheCreationInputTokens": 10,
                    "contextWindow": 200000,
                }
            },
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SigintExit as exc:
        raise SystemExit(exc.code)
