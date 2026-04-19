from __future__ import annotations

import json


class _CreateMessage:
    def user(self, content):
        return json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": content},
            },
            separators=(",", ":"),
        ) + "\n"

    def approve(self, tool_use_id):
        return json.dumps(
            {"type": "approve", "tool_use_id": tool_use_id},
            separators=(",", ":"),
        ) + "\n"

    def deny(self, tool_use_id):
        return json.dumps(
            {"type": "deny", "tool_use_id": tool_use_id},
            separators=(",", ":"),
        ) + "\n"

    def tool_result(self, tool_use_id, content):
        return json.dumps(
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            },
            separators=(",", ":"),
        ) + "\n"


create_message = _CreateMessage()
