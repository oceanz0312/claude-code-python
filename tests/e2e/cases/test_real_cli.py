from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from claude_code import ClaudeCode

from tests.e2e.config import get_client_options, list_available_auth_modes, load_e2e_config
from tests.e2e.harness import (
    cleanup_path,
    create_empty_plugin_dir,
    create_temp_workspace,
    execute_buffered_case,
    execute_streamed_case,
    get_flag_values,
    get_spawn_event,
    has_flag,
    parse_json_response,
    read_debug_file,
    write_probe_file,
    write_prompt_file,
)


IMAGE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "images"
RED_SQUARE_PATH = str(IMAGE_DIR / "red-square.png")
SHAPES_DEMO_PATH = str(IMAGE_DIR / "shapes-demo.png")
RECEIPT_DEMO_PATH = str(IMAGE_DIR / "receipt-demo.png")


async def initialize_config_state() -> dict[str, object]:
    try:
        auth_modes = list_available_auth_modes()
        return {"auth_modes": auth_modes, "error": None}
    except Exception as error:
        return {"auth_modes": [], "error": error if isinstance(error, Exception) else RuntimeError(str(error))}


_CONFIG_STATE = asyncio.run(initialize_config_state())


async def require_auth_modes() -> list[str]:
    error = _CONFIG_STATE["error"]
    if error is not None:
        raise error
    auth_modes = _CONFIG_STATE["auth_modes"]
    if not auth_modes:
        raise RuntimeError(
            "No real E2E auth path is configured. Set E2E_API_KEY or E2E_AUTH_TOKEN + E2E_BASE_URL env vars before running tests."
        )
    return auth_modes


@unittest.skipIf(_CONFIG_STATE["error"] is None, "E2E env vars are configured")
class RealClaudeCliE2ESetupTests(unittest.TestCase):
    def test_requires_e2e_env_vars(self) -> None:
        error = _CONFIG_STATE["error"]
        assert error is not None
        raise error


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliConfigTests(unittest.TestCase):
    def test_loads_local_secrets_and_default_session_settings(self) -> None:
        config = load_e2e_config()
        self.assertGreater(len(config.model), 0)
        self.assertTrue(config.default_session_options.bare)
        self.assertEqual(config.default_session_options.setting_sources, "")
        self.assertTrue(config.default_session_options.include_partial_messages)


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliAuthPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_the_api_key_path_through_claude_code_options_when_configured(self) -> None:
        modes = await require_auth_modes()
        if "api-key" not in modes:
            return

        result = await execute_buffered_case(
            case_name="auth-api-key",
            auth_mode="api-key",
            poison_host_env=True,
            input=[
                {
                    "type": "text",
                    "text": 'Reply with strict JSON only: {"auth_mode":"api-key","status":"ok","short_answer":"<one short sentence>"}. Do not use markdown.',
                }
            ],
        )

        parsed = parse_json_response(result["turn"].final_response)
        self.assertEqual(parsed["auth_mode"], "api-key")
        self.assertEqual(parsed["status"], "ok")
        self.assertGreater(len(parsed["short_answer"]), 0)
        self.assertTrue(result["turn"].session_id)
        self.assertIsNotNone(result["turn"].usage)

        spawn = get_spawn_event(result["rawEvents"])
        self.assertIn("@anthropic-ai/claude-code/cli.js", spawn.command)

    async def test_runs_the_auth_token_and_base_url_path_through_claude_code_options_when_configured(self) -> None:
        modes = await require_auth_modes()
        if "auth-token" not in modes:
            return

        result = await execute_buffered_case(
            case_name="auth-token-base-url",
            auth_mode="auth-token",
            poison_host_env=True,
            input=[
                {
                    "type": "text",
                    "text": 'Reply with strict JSON only: {"auth_mode":"auth-token","status":"ok","short_answer":"<one short sentence>"}. Do not use markdown.',
                }
            ],
        )

        parsed = parse_json_response(result["turn"].final_response)
        self.assertEqual(parsed["auth_mode"], "auth-token")
        self.assertEqual(parsed["status"], "ok")
        self.assertGreater(len(parsed["short_answer"]), 0)
        self.assertTrue(result["turn"].session_id)


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliSessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_context_across_multiple_run_calls_on_the_same_session(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        config = load_e2e_config()
        client_options = get_client_options(config.secrets, auth_mode)
        client_options.cli_path = str((Path(config.repo_root) / ".." / "agent-sdk" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js").resolve())
        client = ClaudeCode(client_options)
        session = client.start_session(
            type(config.default_session_options)(**{**vars(config.default_session_options), "model": config.model, "raw_event_log": False})
        )

        first = await session.run(
            'Remember this token exactly: E2E_SESSION_TOKEN_314159. Reply with JSON only: {"remembered":"E2E_SESSION_TOKEN_314159"}'
        )
        self.assertEqual(parse_json_response(first.final_response)["remembered"], "E2E_SESSION_TOKEN_314159")

        second = await session.run(
            'What token did I ask you to remember in the previous turn? Reply with JSON only: {"remembered":"<token>"}'
        )
        self.assertEqual(parse_json_response(second.final_response)["remembered"], "E2E_SESSION_TOKEN_314159")
        self.assertTrue(second.session_id)

        resumed = client.resume_session(
            second.session_id,
            type(config.default_session_options)(**{**vars(config.default_session_options), "model": config.model, "raw_event_log": False}),
        )
        resumed_turn = await resumed.run('Repeat the remembered token as JSON only: {"remembered":"<token>"}')
        self.assertEqual(parse_json_response(resumed_turn.final_response)["remembered"], "E2E_SESSION_TOKEN_314159")


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_text_deltas_when_include_partial_messages_is_true(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_streamed_case(
            case_name=f"streaming-partials-{auth_mode}",
            auth_mode=auth_mode,
            input="Count from 1 to 8 in one sentence, but stream naturally.",
            session_options=type(load_e2e_config().default_session_options)(include_partial_messages=True),
        )
        self.assertTrue(any(event.type == "text_delta" for event in result["relayEvents"]))
        self.assertTrue(any(event.type == "turn_complete" for event in result["relayEvents"]))
        self.assertGreater(len(result["finalResponse"]), 0)

    async def test_still_completes_when_include_partial_messages_is_false(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_streamed_case(
            case_name=f"streaming-no-partials-{auth_mode}",
            auth_mode=auth_mode,
            input="Count from 1 to 8 in one sentence.",
            session_options=type(load_e2e_config().default_session_options)(include_partial_messages=False),
        )
        self.assertTrue(any(event.type == "turn_complete" for event in result["relayEvents"]))
        self.assertGreater(len(result["finalResponse"]), 0)


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliImageInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_understands_a_simple_red_square_image(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_buffered_case(
            case_name=f"image-red-square-{auth_mode}",
            auth_mode=auth_mode,
            input=[
                {"type": "text", "text": 'Look at the image and reply with JSON only: {"dominant_color":"<color>","shape":"<shape>","confidence":"<high|medium|low>"}'},
                {"type": "local_image", "path": RED_SQUARE_PATH},
            ],
        )
        parsed = parse_json_response(result["turn"].final_response)
        spawn = get_spawn_event(result["rawEvents"])
        self.assertIn("stream-json", get_flag_values(spawn.args, "--input-format"))
        self.assertFalse(has_flag(spawn.args, "--image"))
        self.assertIn("red", parsed.get("dominant_color", "").lower())
        self.assertIn("square", parsed.get("shape", "").lower())

    async def test_counts_obvious_shapes_from_a_synthetic_image(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_buffered_case(
            case_name=f"image-shapes-{auth_mode}",
            auth_mode=auth_mode,
            input=[
                {"type": "text", "text": 'Count the obvious geometric shapes in the image. Reply with JSON only: {"shape_count":<number>,"shapes":["..."]}'},
                {"type": "local_image", "path": SHAPES_DEMO_PATH},
            ],
        )
        parsed = parse_json_response(result["turn"].final_response)
        self.assertGreaterEqual(parsed.get("shape_count", 0), 3)
        self.assertGreater(len(parsed.get("shapes", [])), 0)

    async def test_extracts_a_visible_snippet_from_a_synthetic_receipt_image(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_buffered_case(
            case_name=f"image-receipt-{auth_mode}",
            auth_mode=auth_mode,
            input=[
                {"type": "text", "text": 'Extract one clearly visible short text snippet from the image. Reply with JSON only: {"snippet":"<text>"}'},
                {"type": "local_image", "path": RECEIPT_DEMO_PATH},
            ],
        )
        parsed = parse_json_response(result["turn"].final_response)
        self.assertGreater(len(parsed.get("snippet", "")), 0)


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliOptionBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_system_prompt_and_append_system_prompt_behavior_to_the_final_output(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        result = await execute_buffered_case(
            case_name=f"system-prompt-{auth_mode}",
            auth_mode=auth_mode,
            input="Reply with JSON only.",
            session_options=type(load_e2e_config().default_session_options)(
                system_prompt="Always respond with JSON containing system_tag=SYS_TAG_ALPHA.",
                append_system_prompt="Also include append_tag=APPEND_TAG_BETA in the JSON.",
            ),
        )
        parsed = parse_json_response(result["turn"].final_response)
        self.assertIn("sys_tag_alpha", parsed.get("system_tag", "").lower())
        self.assertIn("append_tag_beta", parsed.get("append_tag", "").lower())

    async def test_reads_system_prompts_from_files_and_can_access_cwd_and_additional_directories(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        workspace = create_temp_workspace("agent-sdk-e2e")
        extra_dir = create_temp_workspace("agent-sdk-e2e-extra")
        try:
            cwd_file = write_probe_file(workspace, "cwd-probe.txt", "CWD_PROBE_TOKEN\n")
            add_dir_file = write_probe_file(extra_dir, "additional-probe.txt", "ADDITIONAL_PROBE_TOKEN\n")
            system_prompt_file = write_prompt_file(workspace, "system-prompt.txt", "Always include FILE_TAG_GAMMA in your final answer.")
            append_system_prompt_file = write_prompt_file(workspace, "append-prompt.txt", "Also include APPEND_FILE_TAG_DELTA in your final answer.")

            result = await execute_buffered_case(
                case_name=f"file-prompts-and-directories-{auth_mode}",
                auth_mode=auth_mode,
                input=f"Read the file at {cwd_file} and the file at {add_dir_file}. Reply with one JSON object containing cwd_token, additional_token, system_tag, and append_tag.",
                session_options=type(load_e2e_config().default_session_options)(
                    cwd=workspace,
                    additional_directories=[extra_dir],
                    system_prompt_file=system_prompt_file,
                    append_system_prompt_file=append_system_prompt_file,
                ),
            )
            normalized = result["turn"].final_response.lower()
            self.assertIn("cwd_probe_token", normalized)
            self.assertIn("additional_probe_token", normalized)
            self.assertIn("file_tag_gamma", normalized)
            self.assertIn("append_file_tag_delta", normalized)
        finally:
            cleanup_path(workspace)
            cleanup_path(extra_dir)

    async def test_records_tool_restrictions_debug_files_settings_and_plugin_directory_in_the_real_spawn_args(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        workspace = create_temp_workspace("agent-sdk-e2e-debug")
        plugin_dir = create_empty_plugin_dir("agent-sdk-plugin")
        try:
            debug_file = str(Path(workspace) / "claude-debug.log")
            settings = json.dumps({"env": {"E2E_SETTINGS_TAG": "SETTINGS_OK"}}, separators=(",", ":"))
            result = await execute_buffered_case(
                case_name=f"spawn-args-{auth_mode}",
                auth_mode=auth_mode,
                input='Reply with JSON only: {"status":"ok"}',
                session_options=type(load_e2e_config().default_session_options)(
                    allowed_tools=["Read"],
                    disallowed_tools=["Bash"],
                    tools="Read,Edit",
                    settings=settings,
                    plugin_dir=plugin_dir,
                    debug=True,
                    debug_file=debug_file,
                    max_turns=2,
                    max_budget_usd=1,
                    effort="low",
                    fallback_model="opus",
                    permission_mode="dontAsk",
                    no_session_persistence=True,
                    exclude_dynamic_system_prompt_sections=True,
                    disable_slash_commands=True,
                    include_hook_events=True,
                    betas="beta-test",
                    name="e2e-spawn-args",
                ),
            )
            spawn = get_spawn_event(result["rawEvents"])
            self.assertIn("Read", get_flag_values(spawn.args, "--allowedTools"))
            self.assertIn("Bash", get_flag_values(spawn.args, "--disallowedTools"))
            self.assertIn("Read,Edit", get_flag_values(spawn.args, "--tools"))
            self.assertIn(settings, get_flag_values(spawn.args, "--settings"))
            self.assertIn(plugin_dir, get_flag_values(spawn.args, "--plugin-dir"))
            self.assertTrue(has_flag(spawn.args, "--debug"))
            self.assertIn(debug_file, get_flag_values(spawn.args, "--debug-file"))
            self.assertTrue(has_flag(spawn.args, "--no-session-persistence"))
            self.assertTrue(has_flag(spawn.args, "--exclude-dynamic-system-prompt-sections"))
            self.assertTrue(has_flag(spawn.args, "--disable-slash-commands"))
            self.assertTrue(has_flag(spawn.args, "--include-hook-events"))
            self.assertIn("beta-test", get_flag_values(spawn.args, "--betas"))
            self.assertIn("e2e-spawn-args", get_flag_values(spawn.args, "--name"))
            self.assertGreater(len(read_debug_file(debug_file)), 0)
        finally:
            cleanup_path(workspace)
            cleanup_path(plugin_dir)


@unittest.skipIf(_CONFIG_STATE["error"] is not None, "E2E env vars are missing")
class RealClaudeCliSessionModesAndAgentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_configured_agent_identity_and_no_session_persistence_blocks_implicit_reuse(self) -> None:
        modes = await require_auth_modes()
        auth_mode = modes[0]
        first = await execute_buffered_case(
            case_name=f"agent-role-{auth_mode}",
            auth_mode=auth_mode,
            input='Reply with JSON only: {"role":"<role>","status":"ok"}',
            session_options=type(load_e2e_config().default_session_options)(
                agents={
                    "reviewer": {
                        "description": "Always identify yourself as reviewer-agent.",
                        "prompt": "When answering, include reviewer-agent in a JSON field named role.",
                    }
                },
                agent="reviewer",
                no_session_persistence=True,
            ),
        )
        self.assertIn("reviewer-agent", first["turn"].final_response.lower())

        second = await execute_buffered_case(
            case_name=f"no-session-persistence-{auth_mode}",
            auth_mode=auth_mode,
            input='What token did I ask you to remember previously? Reply with JSON only: {"remembered":"<token or none>"}',
            session_options=load_e2e_config().default_session_options.__class__(no_session_persistence=True),
        )
        self.assertNotIn("e2e_session_token_314159", second["turn"].final_response.lower())


if __name__ == "__main__":
    unittest.main()
