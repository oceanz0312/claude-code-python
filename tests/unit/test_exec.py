from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from claude_code._exec import ClaudeCodeExec
from claude_code._options import RawClaudeEvent, SessionOptions


FAKE_CLAUDE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py")
TEST_CWD = str(Path(__file__).resolve().parent)
RED_SQUARE_IMAGE = str(Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "images" / "red-square.png")
SHAPES_IMAGE = str(Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "images" / "shapes-demo.png")
INSPECT_PROMPT = "__inspect_exec_options__"
RAW_EVENTS_PROMPT = "__inspect_raw_events__"
PARENT_ENV_KEY = "INSPECT_INHERITED_ENV"

EXPLICIT_API_KEY = "explicit-api-key"
EXPLICIT_AUTH_TOKEN = "explicit-auth-token"
EXPLICIT_BASE_URL = "https://explicit.example.com"


async def inspect_exec(
    *,
    exec_obj: ClaudeCodeExec | None = None,
    session_options: SessionOptions | None = None,
    resume_session_id: str | None = None,
    continue_session: bool = False,
    images: list[str] | None = None,
    input_items: list[dict[str, str]] | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    exec_instance = exec_obj or ClaudeCodeExec(FAKE_CLAUDE)
    lines: list[str] = []

    await exec_instance.run(
        input=INSPECT_PROMPT,
        cli_path=FAKE_CLAUDE,
        session_options=session_options or SessionOptions(),
        resume_session_id=resume_session_id,
        continue_session=continue_session,
        images=images,
        input_items=input_items,
        env=env,
        on_line=lines.append,
    )

    result_line = next((line for line in lines if json.loads(line)["type"] == "result"), None)
    if result_line is None:
        raise AssertionError("Missing result line from fake CLI")

    return json.loads(result_line)["inspection"]


def get_flag_values(args: list[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, value in enumerate(args):
        if value == flag and index + 1 < len(args):
            values.append(args[index + 1])
    return values


def has_flag(args: list[str], flag: str) -> bool:
    return flag in args


class ClaudeCodeExecTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_inherited_env = os.environ.get(PARENT_ENV_KEY)
        self.original_anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.original_anthropic_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        self.original_anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL")

    def tearDown(self) -> None:
        if self.original_inherited_env is None:
            os.environ.pop(PARENT_ENV_KEY, None)
        else:
            os.environ[PARENT_ENV_KEY] = self.original_inherited_env

        if self.original_anthropic_api_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self.original_anthropic_api_key

        if self.original_anthropic_auth_token is None:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        else:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = self.original_anthropic_auth_token

        if self.original_anthropic_base_url is None:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        else:
            os.environ["ANTHROPIC_BASE_URL"] = self.original_anthropic_base_url

    async def test_yields_ndjson_lines_from_fake_cli(self) -> None:
        exec_obj = ClaudeCodeExec(FAKE_CLAUDE)
        lines: list[str] = []

        await exec_obj.run(
            input="hello",
            cli_path=FAKE_CLAUDE,
            session_options=SessionOptions(dangerously_skip_permissions=True),
            resume_session_id=None,
            on_line=lines.append,
        )

        self.assertGreater(len(lines), 0)
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("type", parsed)

    async def test_enables_default_streaming_flags_unless_explicitly_disabled(self) -> None:
        inspection = await inspect_exec()

        self.assertEqual(get_flag_values(inspection["args"], "-p"), [INSPECT_PROMPT])
        self.assertEqual(get_flag_values(inspection["args"], "--input-format"), [])
        self.assertEqual(get_flag_values(inspection["args"], "--output-format"), ["stream-json"])
        self.assertTrue(has_flag(inspection["args"], "--verbose"))
        self.assertTrue(has_flag(inspection["args"], "--include-partial-messages"))

    async def test_omits_default_on_flags_when_verbose_and_partial_messages_are_disabled(self) -> None:
        inspection = await inspect_exec(
            session_options=SessionOptions(verbose=False, include_partial_messages=False)
        )

        self.assertFalse(has_flag(inspection["args"], "--verbose"))
        self.assertFalse(has_flag(inspection["args"], "--include-partial-messages"))

    async def test_applies_precedence_for_continue_permission_mode_and_system_prompt_source(self) -> None:
        inspection = await inspect_exec(
            continue_session=True,
            resume_session_id="resume-me",
            session_options=SessionOptions(
                system_prompt="inline prompt",
                system_prompt_file="/tmp/system-prompt.txt",
                permission_mode="plan",
                dangerously_skip_permissions=True,
            ),
        )

        self.assertTrue(has_flag(inspection["args"], "--continue"))
        self.assertEqual(get_flag_values(inspection["args"], "--resume"), [])
        self.assertEqual(get_flag_values(inspection["args"], "--system-prompt"), ["inline prompt"])
        self.assertEqual(get_flag_values(inspection["args"], "--system-prompt-file"), [])
        self.assertTrue(has_flag(inspection["args"], "--dangerously-skip-permissions"))
        self.assertEqual(get_flag_values(inspection["args"], "--permission-mode"), [])

    async def test_expands_repeated_flags_for_list_style_options_and_uses_stream_json_stdin_for_images(self) -> None:
        inspection = await inspect_exec(
            input_items=[
                {"type": "text", "text": INSPECT_PROMPT},
                {"type": "local_image", "path": RED_SQUARE_IMAGE},
                {"type": "local_image", "path": SHAPES_IMAGE},
            ],
            session_options=SessionOptions(
                additional_directories=["/repo/packages/a", "/repo/packages/b"],
                allowed_tools=["Read", "Edit"],
                disallowed_tools=["Bash", "Write"],
                mcp_config=["mcp-a.json", "mcp-b.json"],
                plugin_dir=["plugins/a", "plugins/b"],
            ),
        )

        self.assertEqual(get_flag_values(inspection["args"], "--add-dir"), ["/repo/packages/a", "/repo/packages/b"])
        self.assertEqual(get_flag_values(inspection["args"], "--allowedTools"), ["Read", "Edit"])
        self.assertEqual(get_flag_values(inspection["args"], "--disallowedTools"), ["Bash", "Write"])
        self.assertEqual(get_flag_values(inspection["args"], "--mcp-config"), ["mcp-a.json", "mcp-b.json"])
        self.assertEqual(get_flag_values(inspection["args"], "--plugin-dir"), ["plugins/a", "plugins/b"])
        self.assertEqual(get_flag_values(inspection["args"], "--input-format"), ["stream-json"])
        self.assertEqual(get_flag_values(inspection["args"], "-p"), ["--input-format"])
        self.assertEqual(inspection["input"]["prompt"], INSPECT_PROMPT)
        self.assertEqual(inspection["input"]["imageCount"], 2)
        self.assertEqual(inspection["input"]["inputFormat"], "stream-json")

    async def test_passes_scalar_flags_through_and_serializes_object_agents(self) -> None:
        inspection = await inspect_exec(
            session_options=SessionOptions(
                model="sonnet",
                cwd=TEST_CWD,
                max_turns=7,
                max_budget_usd=1.5,
                append_system_prompt="append this",
                append_system_prompt_file="/tmp/append.txt",
                tools="Read,Write",
                permission_prompt_tool="mcp__permissions__prompt",
                mcp_config="mcp-single.json",
                strict_mcp_config=True,
                effort="max",
                fallback_model="opus",
                bare=True,
                no_session_persistence=True,
                chrome=False,
                agents={
                    "reviewer": {
                        "description": "Review code changes",
                        "tools": ["Read"],
                        "maxTurns": 2,
                    }
                },
                agent="reviewer",
                name="review session",
                settings='{"source":"test"}',
                setting_sources="user,project",
                include_hook_events=True,
                betas="beta-one,beta-two",
                worktree="feature/review",
                disable_slash_commands=True,
                exclude_dynamic_system_prompt_sections=True,
                debug="sdk",
                debug_file="/tmp/claude-debug.log",
            )
        )

        self.assertEqual(get_flag_values(inspection["args"], "--model"), ["sonnet"])
        self.assertEqual(get_flag_values(inspection["args"], "--cd"), [])
        self.assertEqual(inspection["cwd"], TEST_CWD)
        self.assertEqual(get_flag_values(inspection["args"], "--max-turns"), ["7"])
        self.assertEqual(get_flag_values(inspection["args"], "--max-budget-usd"), ["1.5"])
        self.assertEqual(get_flag_values(inspection["args"], "--append-system-prompt"), ["append this"])
        self.assertEqual(get_flag_values(inspection["args"], "--append-system-prompt-file"), ["/tmp/append.txt"])
        self.assertEqual(get_flag_values(inspection["args"], "--tools"), ["Read,Write"])
        self.assertEqual(get_flag_values(inspection["args"], "--permission-prompt-tool"), ["mcp__permissions__prompt"])
        self.assertEqual(get_flag_values(inspection["args"], "--mcp-config"), ["mcp-single.json"])
        self.assertTrue(has_flag(inspection["args"], "--strict-mcp-config"))
        self.assertEqual(get_flag_values(inspection["args"], "--effort"), ["max"])
        self.assertEqual(get_flag_values(inspection["args"], "--fallback-model"), ["opus"])
        self.assertTrue(has_flag(inspection["args"], "--bare"))
        self.assertTrue(has_flag(inspection["args"], "--no-session-persistence"))
        self.assertTrue(has_flag(inspection["args"], "--no-chrome"))
        self.assertEqual(get_flag_values(inspection["args"], "--agent"), ["reviewer"])
        self.assertEqual(get_flag_values(inspection["args"], "--name"), ["review session"])
        self.assertEqual(get_flag_values(inspection["args"], "--settings"), ['{"source":"test"}'])
        self.assertEqual(get_flag_values(inspection["args"], "--setting-sources"), ["user,project"])
        self.assertTrue(has_flag(inspection["args"], "--include-hook-events"))
        self.assertEqual(get_flag_values(inspection["args"], "--betas"), ["beta-one,beta-two"])
        self.assertEqual(get_flag_values(inspection["args"], "--worktree"), ["feature/review"])
        self.assertTrue(has_flag(inspection["args"], "--disable-slash-commands"))
        self.assertTrue(has_flag(inspection["args"], "--exclude-dynamic-system-prompt-sections"))
        self.assertEqual(get_flag_values(inspection["args"], "--debug"), ["sdk"])
        self.assertEqual(get_flag_values(inspection["args"], "--debug-file"), ["/tmp/claude-debug.log"])

        agents_json = get_flag_values(inspection["args"], "--agents")[0]
        self.assertEqual(
            json.loads(agents_json),
            {"reviewer": {"description": "Review code changes", "tools": ["Read"], "maxTurns": 2}},
        )

    async def test_supports_chrome_debug_and_agents_string_forms(self) -> None:
        raw_agents = '{"worker":{"model":"sonnet"}}'
        inspection = await inspect_exec(
            session_options=SessionOptions(chrome=True, debug=True, agents=raw_agents)
        )

        self.assertTrue(has_flag(inspection["args"], "--chrome"))
        self.assertFalse(has_flag(inspection["args"], "--no-chrome"))
        self.assertTrue(has_flag(inspection["args"], "--debug"))
        self.assertEqual(get_flag_values(inspection["args"], "--agents"), [raw_agents])

    async def test_passes_resume_when_resume_session_id_is_set(self) -> None:
        exec_obj = ClaudeCodeExec(FAKE_CLAUDE)
        lines: list[str] = []

        await exec_obj.run(
            input="resume test",
            cli_path=FAKE_CLAUDE,
            resume_session_id="my-session-123",
            session_options=SessionOptions(dangerously_skip_permissions=True),
            on_line=lines.append,
        )

        result_line = next((line for line in lines if json.loads(line)["type"] == "result"), None)
        self.assertIsNotNone(result_line)
        result = json.loads(result_line)
        self.assertEqual(result["session_id"], "my-session-123")

    async def test_emits_raw_process_events_including_stdout_and_stderr_chunks_and_lines(self) -> None:
        exec_obj = ClaudeCodeExec(FAKE_CLAUDE)
        raw_events: list[RawClaudeEvent] = []
        lines: list[str] = []

        await exec_obj.run(
            input=RAW_EVENTS_PROMPT,
            cli_path=FAKE_CLAUDE,
            session_options=SessionOptions(),
            on_raw_event=raw_events.append,
            on_line=lines.append,
        )

        event_types = [event.type for event in raw_events]
        self.assertIn("spawn", event_types)
        self.assertIn("stdin_closed", event_types)
        self.assertIn("stdout_line", event_types)
        self.assertIn("stderr_chunk", event_types)
        self.assertIn("stderr_line", event_types)
        self.assertIn("exit", event_types)

        spawn_event = next(event for event in raw_events if event.type == "spawn")
        self.assertEqual(spawn_event.command, FAKE_CLAUDE)
        self.assertIn(RAW_EVENTS_PROMPT, spawn_event.args)

        stdout_line = next(event for event in raw_events if event.type == "stdout_line")
        self.assertEqual(json.loads(stdout_line.line)["type"], "result")

        stderr_chunk = next(event for event in raw_events if event.type == "stderr_chunk")
        self.assertEqual(stderr_chunk.chunk, "raw stderr line\n")

        stderr_line = next(event for event in raw_events if event.type == "stderr_line")
        self.assertEqual(stderr_line.line, "raw stderr line")

        exit_event = next(event for event in raw_events if event.type == "exit")
        self.assertEqual(exit_event.code, 0)
        self.assertIsNone(exit_event.signal)

        self.assertEqual(len(lines), 1)

    async def test_uses_explicit_env_override_without_inheriting_os_environ(self) -> None:
        os.environ[PARENT_ENV_KEY] = "from-parent"
        os.environ["ANTHROPIC_API_KEY"] = "from-parent-api-key"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "from-parent-auth-token"
        os.environ["ANTHROPIC_BASE_URL"] = "https://from-parent.example.com"

        exec_obj = ClaudeCodeExec(
            FAKE_CLAUDE,
            {
                "INSPECT_CUSTOM_ENV": "from-override",
                "ANTHROPIC_API_KEY": EXPLICIT_API_KEY,
                "ANTHROPIC_AUTH_TOKEN": EXPLICIT_AUTH_TOKEN,
                "ANTHROPIC_BASE_URL": EXPLICIT_BASE_URL,
            },
        )

        inspection = await inspect_exec(exec_obj=exec_obj)

        self.assertEqual(inspection["env"]["INSPECT_CUSTOM_ENV"], "from-override")
        self.assertIsNone(inspection["env"]["INSPECT_INHERITED_ENV"])
        self.assertEqual(inspection["env"]["ANTHROPIC_API_KEY"], EXPLICIT_API_KEY)
        self.assertEqual(inspection["env"]["ANTHROPIC_AUTH_TOKEN"], EXPLICIT_AUTH_TOKEN)
        self.assertEqual(inspection["env"]["ANTHROPIC_BASE_URL"], EXPLICIT_BASE_URL)

    async def test_allows_per_run_env_to_override_constructor_env(self) -> None:
        exec_obj = ClaudeCodeExec(
            FAKE_CLAUDE,
            {
                "ANTHROPIC_API_KEY": "constructor-key",
                "ANTHROPIC_AUTH_TOKEN": "constructor-token",
                "ANTHROPIC_BASE_URL": "https://constructor.example.com",
            },
        )

        inspection = await inspect_exec(
            exec_obj=exec_obj,
            env={
                "ANTHROPIC_API_KEY": "run-key",
                "ANTHROPIC_AUTH_TOKEN": "run-token",
                "ANTHROPIC_BASE_URL": "https://run.example.com",
            },
        )

        self.assertEqual(inspection["env"]["ANTHROPIC_API_KEY"], "run-key")
        self.assertEqual(inspection["env"]["ANTHROPIC_AUTH_TOKEN"], "run-token")
        self.assertEqual(inspection["env"]["ANTHROPIC_BASE_URL"], "https://run.example.com")

    async def test_does_not_inherit_global_env_when_no_explicit_env_is_provided(self) -> None:
        os.environ[PARENT_ENV_KEY] = "from-parent"
        os.environ["ANTHROPIC_API_KEY"] = "from-parent-api-key"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "from-parent-auth-token"
        os.environ["ANTHROPIC_BASE_URL"] = "https://from-parent.example.com"

        inspection = await inspect_exec(exec_obj=ClaudeCodeExec(FAKE_CLAUDE))

        self.assertIsNone(inspection["env"]["INSPECT_INHERITED_ENV"])
        self.assertIsNone(inspection["env"]["ANTHROPIC_API_KEY"])
        self.assertIsNone(inspection["env"]["ANTHROPIC_AUTH_TOKEN"])
        self.assertIsNone(inspection["env"]["ANTHROPIC_BASE_URL"])

    async def test_merges_constructor_env_with_per_run_env_without_credential_mutual_exclusion(self) -> None:
        exec_obj = ClaudeCodeExec(
            FAKE_CLAUDE,
            {
                "ANTHROPIC_API_KEY": "env-key",
                "ANTHROPIC_AUTH_TOKEN": "env-token",
                "ANTHROPIC_BASE_URL": "https://env.example.com",
                "INSPECT_CUSTOM_ENV": "from-constructor",
            },
        )

        inspection = await inspect_exec(exec_obj=exec_obj, env={"INSPECT_CUSTOM_ENV": "from-run"})

        self.assertEqual(inspection["env"]["ANTHROPIC_API_KEY"], "env-key")
        self.assertEqual(inspection["env"]["ANTHROPIC_AUTH_TOKEN"], "env-token")
        self.assertEqual(inspection["env"]["ANTHROPIC_BASE_URL"], "https://env.example.com")
        self.assertEqual(inspection["env"]["INSPECT_CUSTOM_ENV"], "from-run")


if __name__ == "__main__":
    unittest.main()
