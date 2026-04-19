from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class TimestampedRawEvent:
    timestamp: str
    event: Any


@dataclass
class CaseArtifactPayload:
    case_name: str
    auth_mode: str
    artifact_dir: str
    input_summary: dict[str, Any]
    session_options_summary: dict[str, Any]
    raw_events: list[TimestampedRawEvent]
    relay_events: list[Any]
    final_response: str
    metadata: Optional[dict[str, Any]] = None


_RUN_ID = f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z').replace(':', '-').replace('.', '-')}-{os.getpid()}"


def create_artifact_dir(artifact_root: str, case_name: str) -> str:
    directory = Path(artifact_root) / _RUN_ID / _sanitize_case_name(case_name)
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def write_case_artifacts(payload: CaseArtifactPayload) -> None:
    artifact_dir = Path(payload.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    raw_event_log_files = sorted(
        name
        for name in os.listdir(artifact_dir)
        if name.endswith(".ndjson") and name != "raw-events.ndjson"
    )

    _write_text(artifact_dir / "input.json", json.dumps(payload.input_summary, ensure_ascii=False, indent=2) + "\n")
    _write_text(
        artifact_dir / "relay-events.json",
        json.dumps(_json_ready(payload.relay_events), ensure_ascii=False, indent=2) + "\n",
    )
    raw_event_text = ""
    if payload.raw_events:
        raw_event_text = "\n".join(
            json.dumps(_json_ready(event), ensure_ascii=False, separators=(",", ":"))
            for event in payload.raw_events
        ) + "\n"
    _write_text(artifact_dir / "raw-events.ndjson", raw_event_text)
    _write_text(artifact_dir / "final-response.txt", payload.final_response)

    summary = {
        "caseName": payload.case_name,
        "authMode": payload.auth_mode,
        "artifactDir": payload.artifact_dir,
        "rawEventCount": len(payload.raw_events),
        "relayEventCount": len(payload.relay_events),
        "sessionOptionsSummary": payload.session_options_summary,
        "inputSummary": payload.input_summary,
        "rawEventLogFiles": raw_event_log_files,
        "metadata": payload.metadata or {},
    }
    _write_text(artifact_dir / "summary.json", json.dumps(_json_ready(summary), ensure_ascii=False, indent=2) + "\n")
    _write_text(
        artifact_dir / "terminal-transcript.txt",
        _build_terminal_transcript(payload, raw_event_log_files),
    )


def _build_terminal_transcript(payload: CaseArtifactPayload, raw_event_log_files: list[str]) -> str:
    lines = [
        f"[E2E] case={payload.case_name}",
        f"[E2E] auth_mode={payload.auth_mode}",
        f"[E2E] options={json.dumps(_json_ready(payload.session_options_summary), ensure_ascii=False, separators=(',', ':'))}",
        f"[E2E] input={json.dumps(_json_ready(payload.input_summary), ensure_ascii=False, separators=(',', ':'))}",
        f"[E2E] raw_event_count={len(payload.raw_events)}",
        f"[E2E] relay_event_count={len(payload.relay_events)}",
        f"[E2E] raw_event_log_files={','.join(raw_event_log_files) or '<none>'}",
        f"[E2E] final_response={payload.final_response}",
        f"[E2E] artifact_dir={payload.artifact_dir}",
    ]
    return "\n".join(lines) + "\n"


def _sanitize_case_name(case_name: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in case_name)


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _json_ready(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return {key: _json_ready(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
