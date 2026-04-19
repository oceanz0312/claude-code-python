from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from claude_code import ClaudeCode, Input, RawClaudeEvent, SessionOptions, TurnOptions

from .config import AuthMode, get_client_options, load_e2e_config
from .reporters import CaseArtifactPayload, TimestampedRawEvent, create_artifact_dir, write_case_artifacts


class BufferedCaseResult(dict):
    pass


class StreamedCaseResult(dict):
    pass


async def execute_buffered_case(*, case_name: str, auth_mode: AuthMode, input: Input, session_options: Optional[SessionOptions] = None, poison_host_env: bool = False) -> BufferedCaseResult:
    return await with_optional_poisoned_env(poison_host_env, lambda: _execute_buffered_case(case_name, auth_mode, input, session_options))


async def _execute_buffered_case(case_name: str, auth_mode: AuthMode, input: Input, session_options: Optional[SessionOptions]) -> BufferedCaseResult:
    config = load_e2e_config()
    artifact_dir = create_artifact_dir(config.artifact_root, case_name)
    options = _build_session_options(config.default_session_options, config.model, artifact_dir, session_options)
    cli_path = _resolve_repo_cli_path(config.repo_root)
    client_options = get_client_options(config.secrets, auth_mode)
    client_options.cli_path = cli_path
    client = ClaudeCode(client_options)
    session = client.start_session(options)
    raw_events: list[TimestampedRawEvent] = []
    turn = await session.run(input, TurnOptions(on_raw_event=_create_raw_event_collector(raw_events)))
    raw_event_log_files = _collect_raw_event_log_files(artifact_dir)
    write_case_artifacts(
        CaseArtifactPayload(
            case_name=case_name,
            auth_mode=auth_mode,
            artifact_dir=artifact_dir,
            input_summary=_summarize_input(input),
            session_options_summary=_summarize_session_options(options),
            raw_events=raw_events,
            relay_events=[_relay_event_json(event) for event in turn.events],
            final_response=turn.final_response,
            metadata={
                "sessionId": turn.session_id,
                "usage": _usage_json(turn.usage),
                "rawEventLogFiles": raw_event_log_files,
            },
        )
    )
    _print_case_summary(
        case_name=case_name,
        auth_mode=auth_mode,
        artifact_dir=artifact_dir,
        raw_events=raw_events,
        relay_events=[_relay_event_json(event) for event in turn.events],
        final_response=turn.final_response,
        input=input,
        session_options=options,
    )
    return BufferedCaseResult(
        artifactDir=artifact_dir,
        authMode=auth_mode,
        caseName=case_name,
        turn=turn,
        relayEvents=turn.events,
        rawEvents=raw_events,
        rawEventLogFiles=raw_event_log_files,
    )


async def execute_streamed_case(*, case_name: str, auth_mode: AuthMode, input: Input, session_options: Optional[SessionOptions] = None) -> StreamedCaseResult:
    config = load_e2e_config()
    artifact_dir = create_artifact_dir(config.artifact_root, case_name)
    options = _build_session_options(config.default_session_options, config.model, artifact_dir, session_options)
    cli_path = _resolve_repo_cli_path(config.repo_root)
    client_options = get_client_options(config.secrets, auth_mode)
    client_options.cli_path = cli_path
    client = ClaudeCode(client_options)
    session = client.start_session(options)
    raw_events: list[TimestampedRawEvent] = []
    relay_events: list[Any] = []
    final_response = ""

    streamed = await session.run_streamed(input, TurnOptions(on_raw_event=_create_raw_event_collector(raw_events)))
    async for event in streamed.events:
        relay_events.append(event)
        if event.type == "text_delta":
            final_response += event.content

    raw_event_log_files = _collect_raw_event_log_files(artifact_dir)
    relay_events_json = [_relay_event_json(event) for event in relay_events]
    write_case_artifacts(
        CaseArtifactPayload(
            case_name=case_name,
            auth_mode=auth_mode,
            artifact_dir=artifact_dir,
            input_summary=_summarize_input(input),
            session_options_summary=_summarize_session_options(options),
            raw_events=raw_events,
            relay_events=relay_events_json,
            final_response=final_response,
            metadata={"rawEventLogFiles": raw_event_log_files},
        )
    )
    _print_case_summary(
        case_name=case_name,
        auth_mode=auth_mode,
        artifact_dir=artifact_dir,
        raw_events=raw_events,
        relay_events=relay_events_json,
        final_response=final_response,
        input=input,
        session_options=options,
    )
    return StreamedCaseResult(
        artifactDir=artifact_dir,
        authMode=auth_mode,
        caseName=case_name,
        relayEvents=relay_events,
        rawEvents=raw_events,
        finalResponse=final_response,
        rawEventLogFiles=raw_event_log_files,
    )


def create_temp_workspace(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=f"{prefix}-")


def write_probe_file(directory: str, file_name: str, content: str) -> str:
    path = Path(directory) / file_name
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_prompt_file(directory: str, file_name: str, content: str) -> str:
    return write_probe_file(directory, file_name, content)


def create_empty_plugin_dir(prefix: str) -> str:
    return create_temp_workspace(prefix)


def cleanup_path(target_path: str) -> None:
    import shutil
    shutil.rmtree(target_path, ignore_errors=True)


def parse_json_response(text: str) -> Any:
    normalized = _strip_code_fence(text)
    try:
        return json.loads(normalized)
    except Exception:
        extracted = _extract_first_json_value(normalized)
        if extracted is None:
            raise SyntaxError("JSON Parse error: Unable to parse JSON string")
        return json.loads(extracted)


def get_spawn_event(raw_events: list[TimestampedRawEvent]) -> RawClaudeEvent:
    for entry in raw_events:
        if entry.event.type == "spawn":
            return entry.event
    raise RuntimeError("Missing spawn event in raw event stream.")


def get_flag_values(args: list[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, value in enumerate(args):
        if value == flag and index + 1 < len(args):
            values.append(args[index + 1])
    return values


def has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def read_debug_file(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def list_artifact_files(directory: str) -> list[str]:
    return sorted(os.listdir(directory))


async def with_optional_poisoned_env(enabled: Optional[bool], fn: Callable[[], Any]) -> Any:
    if not enabled:
        return await fn()
    original_api_key = os.environ.get("ANTHROPIC_API_KEY")
    original_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    original_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    os.environ["ANTHROPIC_API_KEY"] = "host-api-key-should-not-leak"
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "host-auth-token-should-not-leak"
    os.environ["ANTHROPIC_BASE_URL"] = "https://host-base-url-should-not-leak.invalid"
    try:
        return await fn()
    finally:
        _restore_env("ANTHROPIC_API_KEY", original_api_key)
        _restore_env("ANTHROPIC_AUTH_TOKEN", original_auth_token)
        _restore_env("ANTHROPIC_BASE_URL", original_base_url)


def _restore_env(key: str, value: Optional[str]) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def _build_session_options(defaults: SessionOptions, model: str, artifact_dir: str, overrides: Optional[SessionOptions]) -> SessionOptions:
    merged_values = {key: value for key, value in vars(defaults).items() if value is not None}
    merged_values["model"] = model
    merged_values["raw_event_log"] = artifact_dir
    if overrides is not None:
        for key, value in vars(overrides).items():
            if value is not None:
                merged_values[key] = value
    return SessionOptions(**merged_values)


def _create_raw_event_collector(raw_events: list[TimestampedRawEvent]) -> Callable[[RawClaudeEvent], None]:
    from datetime import datetime, timezone

    def collector(event: RawClaudeEvent) -> None:
        raw_events.append(
            TimestampedRawEvent(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                event=event,
            )
        )

    return collector


def _summarize_input(input: Input) -> dict[str, Any]:
    if isinstance(input, str):
        return {"prompt": input}
    return {
        "items": [
            {"type": item["type"], "text": item["text"]} if item["type"] == "text" else {"type": item["type"], "path": item["path"]}
            for item in input
        ]
    }


def _summarize_session_options(options: SessionOptions) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    fields = [
        ("model", options.model),
        ("cwd", options.cwd),
        ("additionalDirectories", options.additional_directories),
        ("maxTurns", options.max_turns),
        ("maxBudgetUsd", options.max_budget_usd),
        ("permissionMode", options.permission_mode),
        ("dangerouslySkipPermissions", options.dangerously_skip_permissions),
        ("allowedTools", options.allowed_tools),
        ("disallowedTools", options.disallowed_tools),
        ("tools", options.tools),
        ("mcpConfig", options.mcp_config),
        ("strictMcpConfig", options.strict_mcp_config),
        ("effort", options.effort),
        ("fallbackModel", options.fallback_model),
        ("bare", options.bare),
        ("noSessionPersistence", options.no_session_persistence),
        ("chrome", options.chrome),
        ("agent", options.agent),
        ("name", options.name),
        ("settings", options.settings),
        ("settingSources", options.setting_sources),
        ("verbose", options.verbose),
        ("includePartialMessages", options.include_partial_messages),
        ("includeHookEvents", options.include_hook_events),
        ("betas", options.betas),
        ("worktree", options.worktree),
        ("disableSlashCommands", options.disable_slash_commands),
        ("pluginDir", options.plugin_dir),
        ("excludeDynamicSystemPromptSections", options.exclude_dynamic_system_prompt_sections),
        ("debug", options.debug),
        ("debugFile", options.debug_file),
    ]
    for key, value in fields:
        if value is not None:
            summary[key] = value
    return summary


def _collect_raw_event_log_files(artifact_dir: str) -> list[str]:
    return sorted(
        name
        for name in os.listdir(artifact_dir)
        if name.endswith(".ndjson") and name.startswith("claude-raw-events-")
    )


def _strip_code_fence(text: str) -> str:
    trimmed = text.strip()
    if not trimmed.startswith("```"):
        return trimmed
    import re
    match = re.match(r"^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n?```$", trimmed)
    if match and match.group(1):
        return match.group(1).strip()
    return trimmed.removeprefix("```").removesuffix("```").strip()


def _extract_first_json_value(text: str) -> Optional[str]:
    trimmed = text.strip()
    if not trimmed:
        return None
    start = next((index for index, char in enumerate(trimmed) if char in "[{"), -1)
    if start < 0:
        return None
    opening = trimmed[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(trimmed)):
        char = trimmed[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == opening:
            depth += 1
            continue
        if char == closing:
            depth -= 1
            if depth == 0:
                return trimmed[start : index + 1]
    return None


def _print_case_summary(*, case_name: str, auth_mode: str, artifact_dir: str, raw_events: list[TimestampedRawEvent], relay_events: list[Any], final_response: str, input: Input, session_options: SessionOptions) -> None:
    print(f"[E2E] case={case_name}")
    print(f"[E2E] auth_mode={auth_mode}")
    print(f"[E2E] options={json.dumps(_summarize_session_options(session_options), ensure_ascii=False, separators=(',', ':'))}")
    print(f"[E2E] input={json.dumps(_summarize_input(input), ensure_ascii=False, separators=(',', ':'))}")
    print(f"[E2E] raw_event_count={len(raw_events)}")
    print(f"[E2E] relay_event_count={len(relay_events)}")
    print(f"[E2E] final_response={final_response}")
    print(f"[E2E] artifact_dir={artifact_dir}")


def _resolve_repo_cli_path(repo_root: str) -> str:
    cli_path = Path(repo_root) / ".." / "agent-sdk" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli_path = cli_path.resolve()
    if cli_path.exists():
        return str(cli_path)
    raise RuntimeError(f"Unable to find repo-local Claude CLI at {cli_path}")


def _relay_event_json(event: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": event.type}
    if event.type == "session_meta":
        result["model"] = event.model
        return result
    if event.type in ("text_delta", "thinking_delta"):
        result["content"] = event.content
        return result
    if event.type == "tool_use":
        result["toolUseId"] = event.tool_use_id
        result["toolName"] = event.tool_name
        result["input"] = event.input
        return result
    if event.type == "tool_result":
        result["toolUseId"] = event.tool_use_id
        result["output"] = event.output
        result["isError"] = event.is_error
        return result
    if event.type == "turn_complete":
        if event.session_id is not None:
            result["sessionId"] = event.session_id
        if event.cost_usd is not None:
            result["costUsd"] = event.cost_usd
        if event.input_tokens is not None:
            result["inputTokens"] = event.input_tokens
        if event.output_tokens is not None:
            result["outputTokens"] = event.output_tokens
        if event.context_window is not None:
            result["contextWindow"] = event.context_window
        return result
    if event.type == "error":
        result["message"] = event.message
        if event.session_id is not None:
            result["sessionId"] = event.session_id
        return result
    return result


def _usage_json(usage: Any) -> Optional[dict[str, Any]]:
    if usage is None:
        return None
    return {
        "costUsd": usage.cost_usd,
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "contextWindow": usage.context_window,
    }

