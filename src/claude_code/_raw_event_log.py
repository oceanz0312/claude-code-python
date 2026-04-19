from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import threading
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional, Union

from ._options import ProcessErrorEvent, RawClaudeEvent


RawEventLogOption = Optional[Union[bool, str]]


def _current_time() -> datetime:
    return datetime.now(timezone.utc)


def _random_suffix() -> str:
    value = random.random()
    digits = []
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = int(value * (36 ** 8))
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    text = "".join(reversed(digits or ["0"]))
    return text[:6].ljust(6, "0")


def _open_log_file(file_path: str):
    return open(file_path, "a", encoding="utf-8")


class RawEventLogger:
    def __init__(self, file_path: str) -> None:
        self._file_path = file_path
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False
        self._fatal_error = None  # type: Optional[BaseException]
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        handle = None
        try:
            handle = _open_log_file(self._file_path)
            while True:
                item = self._queue.get()
                if item is None:
                    break
                handle.write(item)
            handle.flush()
        except BaseException as error:  # pragma: no cover - worker surface via close
            self._fatal_error = error
        finally:
            if handle is not None:
                try:
                    handle.close()
                except BaseException as error:  # pragma: no cover
                    if self._fatal_error is None:
                        self._fatal_error = error

    def log(self, event: RawClaudeEvent) -> None:
        if self._closed or self._fatal_error is not None:
            return
        record = {
            "timestamp": _current_time().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "event": serialize_raw_claude_event(event),
        }
        self._queue.put(json.dumps(record, separators=(",", ":")) + "\n")

    async def close(self) -> None:
        if self._closed:
            if self._fatal_error is not None:
                raise self._fatal_error
            return
        self._closed = True
        self._queue.put(None)
        await asyncio.to_thread(self._thread.join)
        if self._fatal_error is not None:
            raise self._fatal_error


class _NoopLogger:
    def log(self, event: RawClaudeEvent) -> None:
        return None

    async def close(self) -> None:
        return None


async def create_raw_event_logger(option: RawEventLogOption):
    if not option:
        return _NoopLogger()

    if isinstance(option, str):
        if not os.path.isabs(option):
            raise ValueError('rawEventLog path must be an absolute path, got: "%s"' % option)
        directory = option
    else:
        directory = os.path.abspath(os.path.join(os.getcwd(), "agent_logs"))

    os.makedirs(directory, exist_ok=True)
    file_path = os.path.join(directory, "%s.ndjson" % create_filename())
    return RawEventLogger(file_path)


def create_filename() -> str:
    iso = _current_time().isoformat(timespec="milliseconds").replace("+00:00", "Z")
    iso = iso.replace(":", "-").replace(".", "-")
    return "claude-raw-events-%s-%s-%s" % (iso, os.getpid(), _random_suffix())


def serialize_raw_claude_event(event: RawClaudeEvent) -> dict:
    if isinstance(event, ProcessErrorEvent):
        error = event.error
        return {
            "type": "process_error",
            "error": {
                "name": type(error).__name__ if error is not None else None,
                "message": str(error) if error is not None else None,
                "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__))
                if error is not None
                else None,
            },
        }

    return asdict(event)
