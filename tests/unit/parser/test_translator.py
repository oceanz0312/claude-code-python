from __future__ import annotations

import unittest

from claude_code.parser import Translator
from claude_code.parser.protocol import ClaudeContent, ClaudeEvent, ClaudeMessage, ModelUsageEntry


class TranslatorTests(unittest.TestCase):
    def test_system_init_emits_session_meta_and_captures_state(self) -> None:
        translator = Translator()
        events = translator.translate(
            ClaudeEvent(type="system", subtype="init", session_id="sid-1", model="sonnet")
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "session_meta")
        self.assertEqual(events[0].model, "sonnet")
        self.assertEqual(translator.session_id, "sid-1")
        self.assertEqual(translator.model, "sonnet")

    def test_result_success_emits_turn_complete_with_usage(self) -> None:
        translator = Translator()
        events = translator.translate(
            ClaudeEvent(
                type="result",
                subtype="success",
                session_id="sid-1",
                total_cost_usd=0.5,
                model_usage={
                    "sonnet": ModelUsageEntry(
                        input_tokens=10,
                        output_tokens=5,
                        cache_read_input_tokens=2,
                        cache_creation_input_tokens=1,
                        context_window=200000,
                    )
                },
            )
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.type, "turn_complete")
        self.assertEqual(event.session_id, "sid-1")
        self.assertEqual(event.cost_usd, 0.5)
        self.assertEqual(event.input_tokens, 13)
        self.assertEqual(event.output_tokens, 5)
        self.assertEqual(event.context_window, 200000)

    def test_result_error_emits_error(self) -> None:
        translator = Translator()
        events = translator.translate(
            ClaudeEvent(type="result", subtype="error", result="Something went wrong", session_id="sid-1")
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].message, "Something went wrong")

    def test_assistant_dedup_incremental(self) -> None:
        translator = Translator()
        first = translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(content=[ClaudeContent(type="text", text="Hello")]),
            )
        )
        second = translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(
                    content=[
                        ClaudeContent(type="text", text="Hello"),
                        ClaudeContent(type="text", text=" World"),
                    ]
                ),
            )
        )
        self.assertEqual([event.content for event in first], ["Hello"])
        self.assertEqual([event.content for event in second], [" World"])

    def test_assistant_context_switch_resets_index(self) -> None:
        translator = Translator()
        translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(content=[ClaudeContent(type="text", text="Agent A")]),
            )
        )
        events = translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(content=[ClaudeContent(type="text", text="Agent B")]),
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].content, "Agent B")

    def test_assistant_content_shrink_resets_index(self) -> None:
        translator = Translator()
        translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(
                    content=[
                        ClaudeContent(type="text", text="Hello"),
                        ClaudeContent(type="text", text="World"),
                    ]
                ),
            )
        )
        events = translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(content=[ClaudeContent(type="text", text="Hello")]),
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].content, "Hello")

    def test_user_tool_result_extracts_fields(self) -> None:
        translator = Translator()
        events = translator.translate(
            ClaudeEvent(
                type="user",
                message=ClaudeMessage(
                    content=[
                        ClaudeContent(
                            type="tool_result",
                            tool_use_id="tool_1",
                            is_error=False,
                            content=[{"type": "text", "text": "ok"}],
                        )
                    ]
                ),
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "tool_result")
        self.assertEqual(events[0].tool_use_id, "tool_1")
        self.assertEqual(events[0].output, "ok")

    def test_translate_content_block_types(self) -> None:
        translator = Translator()
        events = translator.translate(
            ClaudeEvent(
                type="assistant",
                message=ClaudeMessage(
                    content=[
                        ClaudeContent(type="thinking", thinking="think"),
                        ClaudeContent(type="tool_use", id="tool_1", name="Read", input={"path": "/tmp/x"}),
                        ClaudeContent(type="tool_result", tool_use_id="tool_1", content="ok", is_error=True),
                        ClaudeContent(type="unknown", text="ignored"),
                    ]
                ),
            )
        )
        self.assertEqual([event.type for event in events], ["thinking_delta", "tool_use", "tool_result"])
        self.assertEqual(events[0].content, "think")
        self.assertEqual(events[1].tool_use_id, "tool_1")
        self.assertEqual(events[2].output, "ok")
        self.assertTrue(events[2].is_error)


if __name__ == "__main__":
    unittest.main()
