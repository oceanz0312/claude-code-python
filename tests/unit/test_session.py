from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_code import ClaudeCode
from claude_code._options import ClaudeCodeOptions, RawClaudeEvent, SessionOptions, TurnOptions
from claude_code._raw_event_log import create_raw_event_logger
from claude_code._session import Session


FAKE_CLAUDE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py")
STDERR_API_ERROR_PROMPT = "__stderr_api_error__"
STDOUT_API_RETRY_AUTH_PROMPT = "__stdout_api_retry_auth__"
RED_SQUARE_IMAGE = str(Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "images" / "red-square.png")


def create_test_client(options: ClaudeCodeOptions | None = None) -> ClaudeCode:
    merged = ClaudeCodeOptions(cli_path=FAKE_CLAUDE)
    if options is not None:
        merged = ClaudeCodeOptions(
            cli_path=options.cli_path or FAKE_CLAUDE,
            env=options.env,
            api_key=options.api_key,
            auth_token=options.auth_token,
            base_url=options.base_url,
        )
    return ClaudeCode(merged)


async def wait_for_abort(signal: object) -> None:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()

    def on_abort(*_: object) -> None:
        if not future.done():
            future.set_result(None)

    if getattr(signal, "aborted", False):
        return

    add = getattr(signal, "addEventListener", None)
    remove = getattr(signal, "removeEventListener", None)
    if callable(add):
        add("abort", on_abort)
        try:
            await future
        finally:
            if callable(remove):
                remove("abort", on_abort)
        return

    event = getattr(signal, "wait", None)
    if callable(event):
        await event()
        return

    raise RuntimeError("Unsupported abort signal in test helper")


class DelayedErrorExec:
    def __init__(self, error: Exception, delay_ms: int = 10) -> None:
        self.delay_ms = delay_ms
        self.error = error

    async def run(self, **_: object) -> None:
        await asyncio.sleep(self.delay_ms / 1000)
        raise self.error


class AwaitAbortExec:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.signal = None

    async def run(self, **kwargs: object) -> None:
        self.signal = kwargs.get("signal")
        self.started.set()
        if self.signal is None:
            return
        if getattr(self.signal, "aborted", False):
            return
        await wait_for_abort(self.signal)


class ManualAbortSignal:
    def __init__(self) -> None:
        self.aborted = False
        self.reason = None
        self.add_count = 0
        self.remove_count = 0
        self._listeners: list = []

    def addEventListener(self, event_type: str, listener, options=None) -> None:
        if event_type == "abort":
            self.add_count += 1
            self._listeners.append(listener)

    def removeEventListener(self, event_type: str, listener, options=None) -> None:
        if event_type == "abort":
            self.remove_count += 1
            self._listeners = [item for item in self._listeners if item != listener]

    def abort(self, reason=None) -> None:
        self.aborted = True
        self.reason = reason
        for listener in list(self._listeners):
            listener()


class SessionRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_a_complete_turn_with_final_response_and_usage(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        turn = await session.run("hello world")

        self.assertEqual(turn.final_response, "Here is my response.")
        self.assertGreater(len(turn.events), 0)
        self.assertEqual(turn.session_id, "test-session-001")
        self.assertIsNotNone(turn.usage)
        assert turn.usage is not None
        self.assertGreater(turn.usage.cost_usd or 0, 0)
        self.assertGreater(turn.usage.input_tokens or 0, 0)
        self.assertGreater(turn.usage.output_tokens or 0, 0)

    async def test_captures_session_id_from_session_meta(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        await session.run("hello")

        self.assertEqual(session.id, "test-session-001")

    async def test_throws_on_error_response(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        with self.assertRaisesRegex(Exception, "Something went wrong"):
            await session.run("force-error")

    async def test_can_fail_fast_on_fatal_cli_api_errors_written_to_stderr(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        start = asyncio.get_running_loop().time()
        with self.assertRaisesRegex(Exception, "API Error: 502"):
            await session.run(STDERR_API_ERROR_PROMPT, TurnOptions(fail_fast_on_cli_api_error=True))
        duration_ms = (asyncio.get_running_loop().time() - start) * 1000
        self.assertLess(duration_ms, 1000)

    async def test_can_fail_fast_on_fatal_cli_api_retry_events_written_to_stdout(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        start = asyncio.get_running_loop().time()
        with self.assertRaisesRegex(Exception, "authentication_failed"):
            await session.run(STDOUT_API_RETRY_AUTH_PROMPT, TurnOptions(fail_fast_on_cli_api_error=True))
        duration_ms = (asyncio.get_running_loop().time() - start) * 1000
        self.assertLess(duration_ms, 1000)

    async def test_supports_multi_turn_via_automatic_resume(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        first = await session.run("__inspect_session_flags__")
        self.assertEqual(first.session_id, "test-session-001")
        self.assertEqual(session.id, "test-session-001")
        self.assertEqual(
            json.loads(first.final_response),
            {"resumeSessionId": None, "continueSession": False},
        )

        second = await session.run("__inspect_session_flags__")
        self.assertEqual(second.session_id, "test-session-001")
        self.assertEqual(
            json.loads(second.final_response),
            {"resumeSessionId": "test-session-001", "continueSession": False},
        )


class SessionRunStreamedTests(unittest.IsolatedAsyncioTestCase):
    async def test_yields_relay_events_as_async_generator(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        streamed = await session.run_streamed("hello world")
        collected = [event async for event in streamed.events]

        self.assertGreater(len(collected), 0)
        types = {event.type for event in collected}
        self.assertIn("session_meta", types)
        self.assertIn("turn_complete", types)

    async def test_streams_text_delta_events_incrementally(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        streamed = await session.run_streamed("hello world")
        text_deltas: list[str] = []
        async for event in streamed.events:
            if event.type == "text_delta":
                text_deltas.append(event.content)

        self.assertEqual(text_deltas, ["Here is ", "my response."])
        self.assertEqual("".join(text_deltas), "Here is my response.")

    async def test_streams_tool_use_and_tool_result_events(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        streamed = await session.run_streamed("hello world")
        has_tool_use = False
        has_tool_result = False
        async for event in streamed.events:
            if event.type == "tool_use":
                has_tool_use = True
            if event.type == "tool_result":
                has_tool_result = True

        self.assertTrue(has_tool_use)
        self.assertTrue(has_tool_result)

    async def test_can_surface_fatal_cli_api_stderr_as_a_relay_event_error(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        streamed = await session.run_streamed(
            STDERR_API_ERROR_PROMPT,
            TurnOptions(fail_fast_on_cli_api_error=True),
        )
        start = asyncio.get_running_loop().time()
        collected = [event async for event in streamed.events]
        duration_ms = (asyncio.get_running_loop().time() - start) * 1000

        self.assertLess(duration_ms, 1000)
        self.assertTrue(any(event.type == "error" for event in collected))
        error_event = next(event for event in collected if event.type == "error")
        self.assertIn("API Error: 502", error_event.message)
        self.assertEqual(error_event.session_id, "test-session-001")

    async def test_can_surface_fatal_cli_api_retry_stdout_events_as_a_relay_event_error(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        streamed = await session.run_streamed(
            STDOUT_API_RETRY_AUTH_PROMPT,
            TurnOptions(fail_fast_on_cli_api_error=True),
        )
        start = asyncio.get_running_loop().time()
        collected = [event async for event in streamed.events]
        duration_ms = (asyncio.get_running_loop().time() - start) * 1000

        self.assertLess(duration_ms, 1000)
        error_event = next(event for event in collected if event.type == "error")
        self.assertIn("authentication_failed", error_event.message)
        self.assertIn("status 401", error_event.message)
        self.assertEqual(error_event.session_id, "test-session-001")


class SessionModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resumes_with_given_session_id(self) -> None:
        claude = create_test_client()
        session = claude.resume_session("my-custom-session", SessionOptions(dangerously_skip_permissions=True))

        turn = await session.run("continue")
        self.assertEqual(turn.session_id, "my-custom-session")

    async def test_uses_continue_flag(self) -> None:
        claude = create_test_client()
        session = claude.continue_session(SessionOptions(dangerously_skip_permissions=True))

        turn = await session.run("continue from last")
        self.assertTrue(turn.final_response)

    async def test_accepts_user_input_array_with_text(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        turn = await session.run([
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
        ])
        self.assertTrue(turn.final_response)

    async def test_sends_local_image_items_through_stream_json_stdin_instead_of_image_flag(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        raw_stdout_lines: list[str] = []
        await session.run(
            [
                {"type": "text", "text": "__inspect_exec_options__"},
                {"type": "local_image", "path": RED_SQUARE_IMAGE},
            ],
            TurnOptions(
                on_raw_event=lambda event: raw_stdout_lines.append(event.line)
                if event.type == "stdout_line"
                else None
            ),
        )

        result_line = next((line for line in raw_stdout_lines if json.loads(line)["type"] == "result"), None)
        self.assertIsNotNone(result_line)
        inspection = json.loads(result_line)["inspection"]
        self.assertIn("--input-format", inspection["args"])
        self.assertNotIn("--image", inspection["args"])
        self.assertEqual(inspection["input"]["inputFormat"], "stream-json")
        self.assertEqual(inspection["input"]["imageCount"], 1)
        self.assertEqual(inspection["input"]["prompt"], "__inspect_exec_options__")

    async def test_aborts_a_running_session(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        signal = asyncio.Event()
        async def abort_soon() -> None:
            await asyncio.sleep(0.1)
            signal.set()

        asyncio.create_task(abort_soon())
        with self.assertRaises(Exception):
            await session.run("slow-run", TurnOptions(signal=signal))

    async def test_passes_only_explicit_env_from_claude_code_options_into_the_cli_process(self) -> None:
        claude = create_test_client(
            ClaudeCodeOptions(
                api_key="global-key",
                auth_token="global-token",
                base_url="https://global.example.com",
            )
        )
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        raw_stdout_lines: list[str] = []
        streamed = await session.run_streamed(
            "__inspect_exec_options__",
            TurnOptions(
                on_raw_event=lambda event: raw_stdout_lines.append(event.line)
                if event.type == "stdout_line"
                else None
            ),
        )
        async for _event in streamed.events:
            pass

        result_line = next((line for line in raw_stdout_lines if json.loads(line)["type"] == "result"), None)
        self.assertIsNotNone(result_line)
        inspection = json.loads(result_line)["inspection"]
        self.assertEqual(inspection["env"]["ANTHROPIC_API_KEY"], "global-key")
        self.assertEqual(inspection["env"]["ANTHROPIC_AUTH_TOKEN"], "global-token")
        self.assertEqual(inspection["env"]["ANTHROPIC_BASE_URL"], "https://global.example.com")

    async def test_forwards_turn_options_on_raw_event_through_run_streamed(self) -> None:
        claude = create_test_client()
        session = claude.start_session(SessionOptions(dangerously_skip_permissions=True))

        raw_events: list[RawClaudeEvent] = []
        streamed = await session.run_streamed(
            "__inspect_raw_events__",
            TurnOptions(on_raw_event=raw_events.append),
        )
        async for _event in streamed.events:
            pass

        self.assertTrue(any(event.type == "stdout_line" for event in raw_events))
        self.assertTrue(any(event.type == "stderr_line" for event in raw_events))

    async def test_writes_raw_event_logs_as_ndjson_when_enabled(self) -> None:
        with TemporaryDirectory(prefix="agent-sdk-raw-events-") as temp_dir:
            claude = create_test_client()
            session = claude.start_session(
                SessionOptions(dangerously_skip_permissions=True, raw_event_log=temp_dir)
            )

            streamed = await session.run_streamed("__inspect_raw_events__")
            async for _event in streamed.events:
                pass

            files = list(Path(temp_dir).iterdir())
            self.assertEqual(len(files), 1)
            records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").strip().splitlines()]
            self.assertGreater(len(records), 0)
            self.assertTrue(all(isinstance(record["timestamp"], str) for record in records))
            event_types = [record["event"]["type"] for record in records]
            self.assertIn("spawn", event_types)
            self.assertIn("stdout_line", event_types)
            self.assertIn("stderr_chunk", event_types)
            self.assertIn("stderr_line", event_types)
            self.assertIn("exit", event_types)


class SessionInternalBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_a_pending_streamed_iterator_when_processing_fails(self) -> None:
        session = Session(DelayedErrorExec(RuntimeError("delayed stream failure")), ClaudeCodeOptions(), SessionOptions())
        streamed = await session.run_streamed("hello")
        iterator = streamed.events.__aiter__()
        with self.assertRaisesRegex(Exception, "delayed stream failure"):
            await iterator.__anext__()

    async def test_merges_abort_signals_without_a_reason_and_cleans_up_listeners(self) -> None:
        exec_obj = AwaitAbortExec()
        session = Session(exec_obj, ClaudeCodeOptions(), SessionOptions())
        external_signal = ManualAbortSignal()

        run_task = asyncio.create_task(
            session.run("hello", TurnOptions(signal=external_signal, fail_fast_on_cli_api_error=True))
        )
        await exec_obj.started.wait()
        self.assertEqual(external_signal.add_count, 1)
        external_signal.abort()
        turn = await run_task
        self.assertEqual(turn.final_response, "")
        self.assertEqual(turn.events, [])
        self.assertTrue(getattr(exec_obj.signal, "aborted", False))
        self.assertEqual(external_signal.remove_count, 1)

    async def test_preserves_abort_reasons_when_merging_abort_signals(self) -> None:
        exec_obj = AwaitAbortExec()
        session = Session(exec_obj, ClaudeCodeOptions(), SessionOptions())
        external_signal = ManualAbortSignal()

        run_task = asyncio.create_task(
            session.run("hello", TurnOptions(signal=external_signal, fail_fast_on_cli_api_error=True))
        )
        await exec_obj.started.wait()
        external_signal.abort("manual-stop")
        await run_task
        self.assertEqual(getattr(exec_obj.signal, "reason", None), "manual-stop")


class RawEventLoggerFromSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_relative_raw_event_log_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, 'rawEventLog path must be an absolute path, got: "relative/raw-events"'):
            await create_raw_event_logger("relative/raw-events")


if __name__ == "__main__":
    unittest.main()
