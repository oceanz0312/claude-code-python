from __future__ import annotations

import unittest

from claude_code.parser import extract_content


class ExtractContentTests(unittest.TestCase):
    def test_handles_none(self) -> None:
        self.assertEqual(extract_content(None), "")

    def test_passes_through_strings(self) -> None:
        self.assertEqual(extract_content("str"), "str")

    def test_joins_text_blocks(self) -> None:
        self.assertEqual(
            extract_content([
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ]),
            "a\nb",
        )

    def test_falls_back_to_stringification(self) -> None:
        self.assertEqual(extract_content(12345), "12345")


if __name__ == "__main__":
    unittest.main()
