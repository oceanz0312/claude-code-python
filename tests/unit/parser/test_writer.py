from __future__ import annotations

import json
import unittest

from claude_code.parser import create_message


class WriterTests(unittest.TestCase):
    def test_user_message(self) -> None:
        payload = create_message.user("hello")
        self.assertTrue(payload.endswith("\n"))
        parsed = json.loads(payload)
        self.assertEqual(parsed, {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
        })

    def test_approve_message(self) -> None:
        payload = create_message.approve("tool_1")
        self.assertEqual(json.loads(payload), {"type": "approve", "tool_use_id": "tool_1"})

    def test_deny_message(self) -> None:
        payload = create_message.deny("tool_1")
        self.assertEqual(json.loads(payload), {"type": "deny", "tool_use_id": "tool_1"})

    def test_tool_result_message(self) -> None:
        payload = create_message.tool_result("tool_1", [{"type": "text", "text": "ok"}])
        self.assertEqual(
            json.loads(payload),
            {
                "type": "tool_result",
                "tool_use_id": "tool_1",
                "content": [{"type": "text", "text": "ok"}],
            },
        )


if __name__ == "__main__":
    unittest.main()
