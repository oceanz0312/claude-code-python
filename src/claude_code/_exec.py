from __future__ import annotations

import asyncio
import base64
import dataclasses
import inspect
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from signal import Signals
from typing import Any, Callable, Optional

from ._options import (
    ExitEvent,
    ProcessErrorEvent,
    RawClaudeEvent,
    SessionOptions,
    SpawnEvent,
    StderrChunkEvent,
    StderrLineEvent,
    StdinClosedEvent,
    StdoutLineEvent,
    UserInput,
)


@dataclass
class ExecArgs:
    input: str
    input_items: Optional[list[UserInput]] = None
    images: Optional[list[str]] = None
    resume_session_id: Optional[str] = None
    continue_session: bool = False
    session_options: Optional[SessionOptions] = None
    cli_path: Optional[str] = None
    env: Optional[dict[str, str]] = None
    signal: Any = None
    raw_event_logger: Any = None
    on_raw_event: Optional[Callable[[RawClaudeEvent], None]] = None
    on_line: Optional[Callable[[str], None]] = None


class ClaudeCodeExec:
    def __init__(self, cli_path: Optional[str] = None, env: Optional[dict[str, str]] = None) -> None:
        self._cli_path = cli_path
        self._env_override = dict(env or {})
        self._default_cli_path: Optional[str] = None

    async def _resolve_cli_path(self) -> str:
        if self._cli_path:
            return self._cli_path
        if self._default_cli_path is None:
            self._default_cli_path = await asyncio.to_thread(_resolve_default_cli_path)
        return self._default_cli_path

    async def run(self, args: Optional[ExecArgs] = None, **kwargs: Any) -> None:
        exec_args = args if isinstance(args, ExecArgs) else ExecArgs(**kwargs)
        stdin_payload = await build_stdin_payload(exec_args)
        command_args = build_args(exec_args, use_stream_json_input=stdin_payload is not None)
        logger = exec_args.raw_event_logger

        def emit_raw_event(event: RawClaudeEvent) -> None:
            if logger is not None:
                logger.log(event)
            if exec_args.on_raw_event is not None:
                exec_args.on_raw_event(event)

        env: dict[str, str] = {**self._env_override, **(exec_args.env or {})}
        if "PATH" not in env and "PATH" in os.environ:
            env["PATH"] = os.environ["PATH"]

        cli_path = exec_args.cli_path or await self._resolve_cli_path()
        cwd = exec_args.session_options.cwd if exec_args.session_options else None
        spawn_command = [cli_path]
        if cli_path.endswith(".py"):
            spawn_command = [sys.executable, cli_path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *spawn_command,
                *command_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except OSError as error:
            emit_raw_event(ProcessErrorEvent(error=error))
            raise

        emit_raw_event(SpawnEvent(command=cli_path, args=list(command_args), cwd=cwd))

        try:
            try:
                if stdin_payload is not None and proc.stdin is not None:
                    proc.stdin.write(stdin_payload.encode("utf-8"))
                    await proc.stdin.drain()
                if proc.stdin is not None:
                    proc.stdin.close()
                    if hasattr(proc.stdin, "wait_closed"):
                        await proc.stdin.wait_closed()
            except Exception:
                pass
            emit_raw_event(StdinClosedEvent())

            stderr_chunks: list[str] = []
            stderr_task = asyncio.create_task(_pump_stderr(proc, stderr_chunks, emit_raw_event))
            cancel_task = (
                asyncio.create_task(_watch_signal(exec_args.signal, proc))
                if exec_args.signal is not None
                else None
            )

            try:
                assert proc.stdout is not None
                while True:
                    raw_line = await proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    emit_raw_event(StdoutLineEvent(line=line))
                    if exec_args.on_line is not None:
                        exec_args.on_line(line)

                await stderr_task
                return_code = await proc.wait()
                exit_signal = None
                exit_code = return_code
                if return_code is not None and return_code < 0:
                    try:
                        exit_signal = Signals(-return_code).name
                    except Exception:
                        exit_signal = str(-return_code)
                    exit_code = None
                emit_raw_event(ExitEvent(code=exit_code, signal=exit_signal))

                if exit_code is not None and exit_code != 0:
                    stderr_text = "".join(stderr_chunks)
                    raise RuntimeError(
                        f"Claude CLI exited with code {exit_code}"
                        + (f": {stderr_text}" if stderr_text else "")
                    )
                if exit_signal is not None:
                    stderr_text = "".join(stderr_chunks)
                    raise RuntimeError(
                        f"Claude CLI exited with signal {exit_signal}"
                        + (f": {stderr_text}" if stderr_text else "")
                    )
            finally:
                if cancel_task is not None:
                    cancel_task.cancel()
                    with contextlib_suppress(asyncio.CancelledError):
                        await cancel_task
                if not stderr_task.done():
                    stderr_task.cancel()
                    with contextlib_suppress(asyncio.CancelledError):
                        await stderr_task
        finally:
            if proc.returncode is None:
                with contextlib_suppress(ProcessLookupError):
                    proc.terminate()
                with contextlib_suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                if proc.returncode is None:
                    with contextlib_suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib_suppress(ProcessLookupError):
                        await proc.wait()


def build_args(args: ExecArgs, *, use_stream_json_input: bool) -> list[str]:
    cmd: list[str] = []
    opts = args.session_options or SessionOptions()

    cmd.append("-p")
    if not use_stream_json_input:
        cmd.append(args.input)

    if use_stream_json_input:
        cmd += ["--input-format", "stream-json"]

    cmd += ["--output-format", "stream-json"]

    if opts.verbose is not False:
        cmd.append("--verbose")

    if opts.include_partial_messages is not False:
        cmd.append("--include-partial-messages")

    if args.continue_session:
        cmd.append("--continue")
    elif args.resume_session_id:
        cmd += ["--resume", args.resume_session_id]

    if opts.session_id:
        cmd += ["--session-id", opts.session_id]
    if opts.fork_session is True:
        cmd.append("--fork-session")

    if opts.model:
        cmd += ["--model", opts.model]

    for directory in opts.additional_directories or []:
        cmd += ["--add-dir", directory]

    if opts.max_turns is not None:
        cmd += ["--max-turns", str(opts.max_turns)]
    if opts.max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(opts.max_budget_usd)]

    if opts.system_prompt:
        cmd += ["--system-prompt", opts.system_prompt]
    elif opts.system_prompt_file:
        cmd += ["--system-prompt-file", opts.system_prompt_file]
    if opts.append_system_prompt:
        cmd += ["--append-system-prompt", opts.append_system_prompt]
    if opts.append_system_prompt_file:
        cmd += ["--append-system-prompt-file", opts.append_system_prompt_file]

    if opts.dangerously_skip_permissions is True:
        cmd.append("--dangerously-skip-permissions")
    elif opts.permission_mode:
        cmd += ["--permission-mode", opts.permission_mode]

    for tool in opts.allowed_tools or []:
        cmd += ["--allowedTools", tool]
    for tool in opts.disallowed_tools or []:
        cmd += ["--disallowedTools", tool]
    if opts.tools is not None:
        cmd += ["--tools", opts.tools]
    if opts.permission_prompt_tool:
        cmd += ["--permission-prompt-tool", opts.permission_prompt_tool]

    mcp_configs = [opts.mcp_config] if isinstance(opts.mcp_config, str) else (opts.mcp_config or [])
    for config in mcp_configs:
        cmd += ["--mcp-config", config]
    if opts.strict_mcp_config is True:
        cmd.append("--strict-mcp-config")

    if opts.effort:
        cmd += ["--effort", opts.effort]
    if opts.fallback_model:
        cmd += ["--fallback-model", opts.fallback_model]

    if opts.bare is True:
        cmd.append("--bare")
    if opts.no_session_persistence is True:
        cmd.append("--no-session-persistence")

    if opts.chrome is True:
        cmd.append("--chrome")
    elif opts.chrome is False:
        cmd.append("--no-chrome")

    if opts.agents is not None:
        agents_str = opts.agents if isinstance(opts.agents, str) else json.dumps(_json_ready(opts.agents))
        cmd += ["--agents", agents_str]
    if opts.agent:
        cmd += ["--agent", opts.agent]

    if opts.name:
        cmd += ["--name", opts.name]

    if opts.settings is not None:
        cmd += ["--settings", opts.settings]

    cmd += ["--setting-sources", opts.setting_sources if opts.setting_sources is not None else ""]

    if opts.include_hook_events is True:
        cmd.append("--include-hook-events")

    if opts.betas:
        cmd += ["--betas", opts.betas]

    if opts.worktree:
        cmd += ["--worktree", opts.worktree]

    if opts.disable_slash_commands is True:
        cmd.append("--disable-slash-commands")

    plugin_dirs = [opts.plugin_dir] if isinstance(opts.plugin_dir, str) else (opts.plugin_dir or [])
    for directory in plugin_dirs:
        cmd += ["--plugin-dir", directory]

    if opts.exclude_dynamic_system_prompt_sections is True:
        cmd.append("--exclude-dynamic-system-prompt-sections")

    if opts.debug is True:
        cmd.append("--debug")
    elif isinstance(opts.debug, str):
        cmd += ["--debug", opts.debug]
    if opts.debug_file:
        cmd += ["--debug-file", opts.debug_file]

    if opts.json_schema is not None:
        schema_str = opts.json_schema if isinstance(opts.json_schema, str) else json.dumps(opts.json_schema)
        cmd += ["--json-schema", schema_str]

    return cmd


async def build_stdin_payload(args: ExecArgs) -> Optional[str]:
    input_items = get_structured_input_items(args)
    if input_items is None:
        return None

    content: list[dict[str, Any]] = []
    for item in input_items:
        if item["type"] == "text":
            text = item.get("text", "")
            if text:
                content.append({"type": "text", "text": text})
            continue

        image_buffer = await asyncio.to_thread(Path(item["path"]).read_bytes)
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": detect_image_media_type(image_buffer, item["path"]),
                    "data": base64.b64encode(image_buffer).decode("ascii"),
                },
            }
        )

    if not content:
        return None

    return (
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": content},
            }
        )
        + "\n"
    )


def get_structured_input_items(args: ExecArgs):
    if args.input_items and any(item["type"] == "local_image" for item in args.input_items):
        return merge_text_items(args.input_items)

    if args.images:
        items: list[UserInput] = []
        if args.input:
            items.append({"type": "text", "text": args.input})
        for image in args.images:
            items.append({"type": "local_image", "path": image})
        return items

    return None


def merge_text_items(items: list[UserInput]):
    merged: list[UserInput] = []
    pending_text: list[str] = []

    def flush_pending_text() -> None:
        if not pending_text:
            return
        merged.append({"type": "text", "text": "\n\n".join(pending_text)})
        pending_text.clear()

    for item in items:
        if item["type"] == "text":
            pending_text.append(item["text"])
            continue
        flush_pending_text()
        merged.append(item)

    flush_pending_text()
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

    extension = Path(file_path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(extension, "image/png")


async def _pump_stderr(
    proc: asyncio.subprocess.Process,
    stderr_chunks: list[str],
    emit_raw_event: Callable[[RawClaudeEvent], None],
) -> None:
    assert proc.stderr is not None
    pending = ""
    while True:
        chunk = await proc.stderr.read(4096)
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        stderr_chunks.append(text)
        emit_raw_event(StderrChunkEvent(chunk=text))
        pending += text
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            emit_raw_event(StderrLineEvent(line=line.rstrip("\r")))
    if pending:
        emit_raw_event(StderrLineEvent(line=pending.rstrip("\r")))


async def _watch_signal(signal: Any, proc: asyncio.subprocess.Process) -> None:
    try:
        await _wait_for_abort(signal)
    except asyncio.CancelledError:
        return
    if proc.returncode is None:
        with contextlib_suppress(ProcessLookupError):
            proc.terminate()


async def _wait_for_abort(signal: Any) -> None:
    if signal is None:
        return

    if _signal_is_aborted(signal):
        return

    add_listener = getattr(signal, "addEventListener", None)
    remove_listener = getattr(signal, "removeEventListener", None)
    if callable(add_listener):
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        def on_abort(*_: object) -> None:
            if not future.done():
                future.set_result(None)

        add_listener("abort", on_abort)
        try:
            await future
        finally:
            if callable(remove_listener):
                remove_listener("abort", on_abort)
        return

    wait_method = getattr(signal, "wait", None)
    if callable(wait_method):
        result = wait_method()
        if inspect.isawaitable(result):
            await result
        return

    raise RuntimeError("Unsupported abort signal")


def _signal_is_aborted(signal: Any) -> bool:
    if getattr(signal, "aborted", False):
        return True
    is_set = getattr(signal, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return False


def _json_ready(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        result: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            current = getattr(value, field.name)
            if current is None:
                continue
            result[_snake_to_camel(field.name)] = _json_ready(current)
        return result
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])




def _resolve_default_cli_path() -> str:
    try:
        result = subprocess.run(
            [
                "node",
                "-e",
                "try{console.log(require.resolve('@anthropic-ai/claude-code/cli.js'))}catch{process.exit(1)}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "claude"


class contextlib_suppress:
    def __init__(self, *exceptions: type[BaseException]) -> None:
        self._exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, self._exceptions)
