from __future__ import annotations

import unittest

from claude_code.parser import parse_line


class ParseLineTests(unittest.TestCase):
    def test_returns_none_for_empty_line(self) -> None:
        self.assertIsNone(parse_line("  \n"))

    def test_returns_none_for_invalid_json(self) -> None:
        self.assertIsNone(parse_line("not json"))

    def test_parses_basic_result_event(self) -> None:
        event = parse_line('{"type":"result","subtype":"success"}')
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.type, "result")
        self.assertEqual(event.subtype, "success")

    def test_preserves_stream_event_payload(self) -> None:
        event = parse_line(
            '{"type":"stream_event","event":{"type":"message_start","message":{"id":"m1"}}}'
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event["type"], "message_start")
        self.assertEqual(event.event["message"]["id"], "m1")

    def test_preserves_api_retry_extension_fields(self) -> None:
        event = parse_line(
            '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":3,'
            '"retry_delay_ms":600,"error_status":401,"error":"authentication_failed"}'
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.attempt, 1)
        self.assertEqual(event.max_retries, 3)
        self.assertEqual(event.retry_delay_ms, 600)
        self.assertEqual(event.error_status, 401)
        self.assertEqual(event.error, "authentication_failed")

    def test_preserves_message_id(self) -> None:
        event = parse_line(
            '{"type":"assistant","message":{"id":"msg-1","content":[{"type":"text","text":"hi"}]}}'
        )
        self.assertIsNotNone(event)
        assert event is not None
        assert event.message is not None
        self.assertEqual(event.message.id, "msg-1")


if __name__ == "__main__":
    unittest.main()
