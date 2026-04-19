from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import claude_code._raw_event_log as raw_event_log_module
from claude_code._options import ProcessErrorEvent, SpawnEvent, StderrChunkEvent
from claude_code._raw_event_log import create_raw_event_logger


class RawEventLoggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_relative_raw_event_log_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, 'rawEventLog path must be an absolute path, got: "relative/raw-events"'):
            await create_raw_event_logger("relative/raw-events")

    async def test_uses_the_default_agent_logs_directory_and_serializes_process_errors(self) -> None:
        with TemporaryDirectory(prefix="agent-sdk-default-log-") as temp_dir:
            original_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                logger = await create_raw_event_logger(True)
                logger.log(ProcessErrorEvent(error=RuntimeError("raw logger boom")))
                await logger.close()
                logger.log(SpawnEvent(command="ignored", args=[]))
                await logger.close()

                log_dir = Path(temp_dir) / "agent_logs"
                files = list(log_dir.iterdir())
                self.assertEqual(len(files), 1)

                records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").strip().splitlines()]
                record = records[0]
                self.assertIsInstance(record["timestamp"], str)
                self.assertEqual(record["event"]["type"], "process_error")
                self.assertEqual(record["event"]["error"]["name"], "RuntimeError")
                self.assertEqual(record["event"]["error"]["message"], "raw logger boom")
                self.assertIn("raw logger boom", record["event"]["error"]["stack"])
            finally:
                os.chdir(original_cwd)

    async def test_waits_for_drain_before_closing_after_a_backpressured_write(self) -> None:
        with TemporaryDirectory(prefix="agent-sdk-drain-log-") as temp_dir:
            logger = await create_raw_event_logger(temp_dir)
            logger.log(StderrChunkEvent(chunk="x" * (1024 * 1024)))
            await logger.close()

            files = list(Path(temp_dir).iterdir())
            self.assertEqual(len(files), 1)
            log_text = files[0].read_text(encoding="utf-8")
            self.assertIn('"type":"stderr_chunk"', log_text.replace(" ", ""))
            self.assertGreater(len(log_text), 1024 * 1024)

    async def test_rethrows_fatal_stream_errors_captured_before_close_completes(self) -> None:
        with TemporaryDirectory(prefix="agent-sdk-close-error-") as temp_dir:
            original_current_time = raw_event_log_module._current_time
            original_random_suffix = raw_event_log_module._random_suffix
            try:
                from datetime import datetime, timezone

                raw_event_log_module._current_time = lambda: datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
                raw_event_log_module._random_suffix = lambda: "4fzzzx"
                blocked_path = Path(temp_dir) / f"claude-raw-events-2026-01-02T03-04-05-678Z-{os.getpid()}-4fzzzx.ndjson"
                blocked_path.mkdir(parents=True)

                logger = await create_raw_event_logger(temp_dir)
                logger.log(SpawnEvent(command="claude", args=[]))

                with self.assertRaisesRegex(Exception, "Is a directory|EISDIR"):
                    await logger.close()
            finally:
                raw_event_log_module._current_time = original_current_time
                raw_event_log_module._random_suffix = original_random_suffix

    async def test_throws_close_errors_when_the_underlying_stream_cannot_open(self) -> None:
        with TemporaryDirectory(prefix="agent-sdk-error-log-") as temp_dir:
            os.chmod(temp_dir, 0o500)
            try:
                logger = await create_raw_event_logger(temp_dir)
                logger.log(SpawnEvent(command="claude", args=[]))
                with self.assertRaises(Exception):
                    await logger.close()
            finally:
                try:
                    os.chmod(temp_dir, 0o700)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
