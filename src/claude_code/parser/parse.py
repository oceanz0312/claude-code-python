from __future__ import annotations

import json
from typing import Optional

from .protocol import ClaudeEvent, claude_event_from_dict


def parse_line(line: str) -> Optional[ClaudeEvent]:
    trimmed = line.strip()
    if not trimmed:
        return None

    try:
        parsed = json.loads(trimmed)
    except (TypeError, ValueError):
        return None

    if not isinstance(parsed, dict):
        return None

    return claude_event_from_dict(parsed)
