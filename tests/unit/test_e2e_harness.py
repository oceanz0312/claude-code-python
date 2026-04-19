from __future__ import annotations

import unittest

from claude_code import SessionOptions
from tests.e2e.harness import _build_session_options, _summarize_session_options


class E2EHarnessSessionOptionsParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = SessionOptions(
            bare=True,
            setting_sources="",
            verbose=True,
            include_partial_messages=True,
            dangerously_skip_permissions=True,
        )

    def test_build_session_options_preserves_default_values_when_overrides_omit_them(self) -> None:
        merged = _build_session_options(
            self.defaults,
            "sonnet",
            "/tmp/e2e-artifacts",
            SessionOptions(
                system_prompt="Always include SYS_TAG_ALPHA.",
                append_system_prompt="Also include APPEND_TAG_BETA.",
            ),
        )

        self.assertEqual(merged.model, "sonnet")
        self.assertEqual(merged.raw_event_log, "/tmp/e2e-artifacts")
        self.assertTrue(merged.bare)
        self.assertEqual(merged.setting_sources, "")
        self.assertTrue(merged.verbose)
        self.assertTrue(merged.include_partial_messages)
        self.assertTrue(merged.dangerously_skip_permissions)
        self.assertEqual(merged.system_prompt, "Always include SYS_TAG_ALPHA.")
        self.assertEqual(merged.append_system_prompt, "Also include APPEND_TAG_BETA.")

    def test_build_session_options_preserves_defaults_while_honoring_explicit_false_overrides(self) -> None:
        merged = _build_session_options(
            self.defaults,
            "sonnet",
            "/tmp/e2e-artifacts",
            SessionOptions(include_partial_messages=False),
        )

        self.assertTrue(merged.bare)
        self.assertEqual(merged.setting_sources, "")
        self.assertTrue(merged.verbose)
        self.assertFalse(merged.include_partial_messages)
        self.assertTrue(merged.dangerously_skip_permissions)

    def test_summarize_session_options_matches_ts_shape_for_default_e2e_settings(self) -> None:
        merged = _build_session_options(
            self.defaults,
            "sonnet",
            "/tmp/e2e-artifacts",
            SessionOptions(
                system_prompt="Always include SYS_TAG_ALPHA.",
                append_system_prompt="Also include APPEND_TAG_BETA.",
            ),
        )

        self.assertEqual(
            _summarize_session_options(merged),
            {
                "model": "sonnet",
                "dangerouslySkipPermissions": True,
                "bare": True,
                "settingSources": "",
                "verbose": True,
                "includePartialMessages": True,
            },
        )

    def test_summarize_session_options_keeps_explicit_false_values(self) -> None:
        summary = _summarize_session_options(SessionOptions(model="sonnet", no_session_persistence=False))

        self.assertEqual(
            summary,
            {
                "model": "sonnet",
                "noSessionPersistence": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
