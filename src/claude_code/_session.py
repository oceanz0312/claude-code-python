from __future__ import annotations

import asyncio
import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional, Union

from ._exec import ClaudeCodeExec, ExecArgs
from ._options import (
    ClaudeCodeOptions,
    RawClaudeEvent,
    SessionOptions,
    StderrChunkEvent,
    StderrLineEvent,
    TurnOptions,
    UserInput,
)
from ._raw_event_log import create_raw_event_logger
from .parser import (
    ClaudeEvent,
    ErrorEvent,
    RelayEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    Translator,
    TurnCompleteEvent,
    parse_line,
)


Input = Union[str, list[UserInput]]


@dataclass
class TurnUsage:
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_window: int | None = None


@dataclass
class Turn:
    events: list[RelayEvent]
    final_response: str
    usage: TurnUsage | None
    session_id: str | None
    structured_output: Any = None


RunResult = Turn


@dataclass
class StreamedTurn:
    events: AsyncIterator[RelayEvent]


RunStreamedResult = StreamedTurn


_SENTINEL = object()
_API_ERROR_RE = re.compile(r"\bAPI Error:", re.IGNORECASE)


class _EventChannel(AsyncIterator[RelayEvent]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._error: BaseException | None = None

    def push(self, event: RelayEvent) -> None:
        self._queue.put_nowait(event)

    def end(self) -> None:
        self._queue.put_nowait(_SENTINEL)

    def set_error(self, err: BaseException) -> None:
        self._error = err
        self._queue.put_nowait(_SENTINEL)

    def __aiter__(self) -> "_EventChannel":
        return self

    async def __anext__(self) -> RelayEvent:
        item = await self._queue.get()
        if item is _SENTINEL:
            if self._error is not None:
                raise self._error
            raise StopAsyncIteration
        return item


@dataclass
class _MergedAbortSignal:
    aborted: bool = False
    reason: Any = None
    _listeners: list[Callable[..., None]] = field(default_factory=list)

    def addEventListener(self, event_type: str, listener, options=None) -> None:
        if event_type == "abort":
            self._listeners.append(listener)

    def removeEventListener(self, event_type: str, listener, options=None) -> None:
        if event_type == "abort":
            self._listeners = [item for item in self._listeners if item != listener]

    def abort(self, reason: Any = None) -> None:
        if self.aborted:
            return
        self.aborted = True
        self.reason = reason
        for listener in list(self._listeners):
            listener()


@dataclass
class _StreamedMessageState:
    text_streamed: bool = False
    thinking_streamed: bool = False


@dataclass
class _StreamState:
    active_message_id: str | None = None
    last_completed_message_id: str | None = None
    messages: dict[str, _StreamedMessageState] = field(default_factory=dict)


class Session:
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
        self._active_tasks: set[asyncio.Task[Any]] = set()

    @property
    def id(self) -> str | None:
        return self._id

    async def run(self, input: Input, turn_options: TurnOptions | None = None) -> Turn:
        options = turn_options or TurnOptions()
        events: list[RelayEvent] = []
        final_response = ""
        usage: TurnUsage | None = None
        session_id = self._id
        stream_error: ErrorEvent | None = None
        structured_output_ref = [None]

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

        await self._process_stream(input, options, structured_output_ref, on_event)

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
        options = turn_options or TurnOptions()
        channel = _EventChannel()
        structured_output_ref = [None]

        async def runner() -> None:
            try:
                await self._process_stream(input, options, structured_output_ref, channel.push)
                channel.end()
            except BaseException as error:
                channel.set_error(error if isinstance(error, Exception) else RuntimeError(str(error)))

        task = asyncio.create_task(runner())
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return StreamedTurn(events=channel)

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

        cli_abort_signal = _MergedAbortSignal() if turn_options.fail_fast_on_cli_api_error else None
        merged_signal, cleanup_abort = _merge_abort_signals(turn_options.signal, cli_abort_signal)

        def handle_raw_event(event: RawClaudeEvent) -> None:
            nonlocal fatal_cli_error, stderr_text
            if turn_options.fail_fast_on_cli_api_error and fatal_cli_error is None:
                stderr_text = _append_stderr_text(stderr_text, event)
                detected_error = _extract_fatal_cli_api_error(stderr_text)
                if detected_error:
                    fatal_cli_error = detected_error
                    if cli_abort_signal is not None:
                        cli_abort_signal.abort()
            if turn_options.on_raw_event is not None:
                turn_options.on_raw_event(event)

        def handle_line(line: str) -> None:
            nonlocal fatal_cli_error
            parsed = parse_line(line)
            if parsed is None:
                return

            if parsed.type == "result":
                structured_output = parsed.raw.get("structured_output")
                if structured_output is not None:
                    structured_output_ref[0] = structured_output

            if turn_options.fail_fast_on_cli_api_error and fatal_cli_error is None:
                detected_error = _extract_fatal_cli_api_error_from_stdout(parsed)
                if detected_error:
                    fatal_cli_error = detected_error
                    if not self._id and isinstance(parsed.session_id, str):
                        self._id = parsed.session_id
                    if cli_abort_signal is not None:
                        cli_abort_signal.abort()
                    on_event(ErrorEvent(message=fatal_cli_error, session_id=self._id or translator.session_id))
                    return

            relay_events = _translate_relay_events(parsed, translator, stream_state)

            if translator.session_id and not self._id:
                self._id = translator.session_id

            for event in relay_events:
                if isinstance(event, TurnCompleteEvent) and event.session_id and not self._id:
                    self._id = event.session_id
                on_event(event)

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
            await self._exec.run(
                input=prompt,
                input_items=input_items,
                images=images or None,
                resume_session_id=resume_id,
                continue_session=continue_session,
                session_options=self._session_options,
                cli_path=self._global_options.cli_path,
                env=self._global_options.env,
                signal=merged_signal,
                raw_event_logger=raw_event_logger,
                on_raw_event=handle_raw_event,
                on_line=handle_line,
            )
        except Exception:
            if fatal_cli_error is not None:
                on_event(ErrorEvent(message=fatal_cli_error, session_id=self._id or translator.session_id))
                return
            raise
        finally:
            await raw_event_logger.close()
            cleanup_abort()
            self._has_run = True


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


def _translate_relay_events(
    parsed: ClaudeEvent,
    translator: Translator,
    state: _StreamState,
) -> list[RelayEvent]:
    if parsed.type == "stream_event":
        return _translate_stream_event(parsed, state)

    relay_events = translator.translate(parsed)
    if parsed.type == "assistant":
        return _suppress_duplicate_assistant_snapshot(parsed, relay_events, state)
    return relay_events


def _translate_stream_event(raw: ClaudeEvent, state: _StreamState) -> list[RelayEvent]:
    event = getattr(raw, "event", None) or {}
    event_type = event.get("type")

    if event_type == "message_start":
        message_id = _get_message_id(event.get("message"))
        if message_id:
            state.active_message_id = message_id
            state.messages.setdefault(message_id, _StreamedMessageState())
        return []

    if event_type == "message_stop":
        message_id = _resolve_stream_event_message_id(event, state)
        if message_id:
            state.messages.setdefault(message_id, _StreamedMessageState())
            state.last_completed_message_id = message_id
            if state.active_message_id == message_id:
                state.active_message_id = None
        return []

    if event_type != "content_block_delta":
        return []

    delta = event.get("delta") or {}
    message_id = _resolve_stream_event_message_id(event, state)
    message_state = state.messages.setdefault(message_id, _StreamedMessageState()) if message_id else None

    if delta.get("type") == "text_delta":
        text = delta.get("text") or ""
        if not text:
            return []
        if message_state is not None:
            message_state.text_streamed = True
        return [TextDeltaEvent(content=text)]

    if delta.get("type") == "thinking_delta":
        thinking = delta.get("thinking") or delta.get("text") or ""
        if not thinking:
            return []
        if message_state is not None:
            message_state.thinking_streamed = True
        return [ThinkingDeltaEvent(content=thinking)]

    return []


def _suppress_duplicate_assistant_snapshot(
    raw: ClaudeEvent,
    relay_events: list[RelayEvent],
    state: _StreamState,
) -> list[RelayEvent]:
    message_id = _get_message_id(raw.message) or state.last_completed_message_id
    if not message_id:
        return relay_events

    message_state = state.messages.get(message_id)
    if message_state is None:
        return relay_events

    result: list[RelayEvent] = []
    for event in relay_events:
        if isinstance(event, TextDeltaEvent) and message_state.text_streamed:
            continue
        if isinstance(event, ThinkingDeltaEvent) and message_state.thinking_streamed:
            continue
        result.append(event)
    return result


def _get_message_id(message: Any) -> str | None:
    if message is None:
        return None
    if dataclasses.is_dataclass(message):
        return getattr(message, "id", None)
    if isinstance(message, dict):
        value = message.get("id")
        return value if isinstance(value, str) else None
    return getattr(message, "id", None)


def _resolve_stream_event_message_id(event: dict[str, Any], state: _StreamState) -> str | None:
    message_id = event.get("message_id")
    if isinstance(message_id, str):
        return message_id
    nested = _get_message_id(event.get("message"))
    if nested:
        return nested
    return state.active_message_id


def _append_stderr_text(stderr_text: str, event: RawClaudeEvent) -> str:
    if isinstance(event, StderrChunkEvent):
        return (stderr_text + event.chunk)[-16384:]
    if isinstance(event, StderrLineEvent):
        return (stderr_text + event.line + "\n")[-16384:]
    return stderr_text


def _extract_fatal_cli_api_error(stderr_text: str) -> str | None:
    match = _API_ERROR_RE.search(stderr_text)
    if not match:
        return None
    tail = stderr_text[match.start() :].strip()
    if not tail:
        return None
    first_line = tail.splitlines()[0].strip()
    return first_line or tail


def _extract_fatal_cli_api_error_from_stdout(parsed: ClaudeEvent) -> str | None:
    if parsed.type != "system" or parsed.subtype != "api_retry":
        return None

    error_status = parsed.error_status if isinstance(parsed.error_status, int) else None
    error = parsed.error.strip() if isinstance(parsed.error, str) and parsed.error.strip() else None
    if error_status is None and error is None:
        return None

    parts = ["API retry aborted"]
    if error_status is not None:
        parts.append(f"status {error_status}")
    if error is not None:
        parts.append(error)
    if isinstance(parsed.attempt, int) and isinstance(parsed.max_retries, int):
        parts.append(f"attempt {parsed.attempt}/{parsed.max_retries}")
    if isinstance(parsed.retry_delay_ms, (int, float)):
        parts.append(f"next retry in {round(parsed.retry_delay_ms)}ms")
    return " | ".join(parts)


def _merge_abort_signals(*signals: Any) -> tuple[Any, Callable[[], None]]:
    active = [signal for signal in signals if signal is not None]
    if not active:
        return None, lambda: None
    if len(active) == 1:
        return active[0], lambda: None

    merged = _MergedAbortSignal()
    cleanup_callbacks: list[Callable[[], None]] = []

    def abort_from(source: Any) -> None:
        if merged.aborted:
            return
        reason = getattr(source, "reason", None)
        merged.abort(reason)

    for signal in active:
        if _signal_is_aborted(signal):
            abort_from(signal)
            return merged, lambda: None

        def on_abort(source: Any = signal) -> None:
            abort_from(source)

        add_listener = getattr(signal, "addEventListener", None)
        remove_listener = getattr(signal, "removeEventListener", None)
        if callable(add_listener):
            add_listener("abort", on_abort)
            if callable(remove_listener):
                cleanup_callbacks.append(lambda source=signal, listener=on_abort: source.removeEventListener("abort", listener))
        else:
            raise RuntimeError("Unsupported abort signal")

    def cleanup() -> None:
        for callback in cleanup_callbacks:
            callback()

    return merged, cleanup


def _signal_is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
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
