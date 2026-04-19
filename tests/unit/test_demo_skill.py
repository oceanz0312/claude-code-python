from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_code import ClaudeCodeOptions, SessionOptions
from demo.skill import (
    DemoSecrets,
    build_client_options,
    build_session_options,
    format_json_line,
    load_demo_secrets,
)


class DemoSkillHelperTests(unittest.TestCase):
    def test_build_client_options_maps_demo_secrets_to_sdk_options(self) -> None:
        options = build_client_options(
            DemoSecrets(
                model='sonnet',
                api_key='demo-key',
                auth_token='demo-token',
                base_url='https://example.invalid/coding/',
            )
        )

        self.assertEqual(
            options,
            ClaudeCodeOptions(
                api_key='demo-key',
                auth_token='demo-token',
                base_url='https://example.invalid/coding/',
            ),
        )

    def test_build_session_options_matches_skill_demo_intent(self) -> None:
        demo_dir = Path('/tmp/claude-code-python/demo')

        options = build_session_options(demo_dir)

        self.assertEqual(
            options,
            SessionOptions(
                cwd=str(demo_dir),
                dangerously_skip_permissions=True,
                plugin_dir=str(demo_dir / 'claude-code-plugin'),
            ),
        )

    def test_load_demo_secrets_reads_e2e_env_exports(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / '.env'
            env_path.write_text(
                '\n'.join([
                    'export E2E_MODEL=sonnet',
                    'export E2E_API_KEY=',
                    'export E2E_AUTH_TOKEN=test-auth-token',
                    'export E2E_BASE_URL=https://example.invalid/coding/',
                    '',
                ]),
                encoding='utf-8',
            )

            secrets = load_demo_secrets(env_path)

            self.assertEqual(
                secrets,
                DemoSecrets(
                    model='sonnet',
                    api_key=None,
                    auth_token='test-auth-token',
                    base_url='https://example.invalid/coding/',
                ),
            )

    def test_load_demo_secrets_ignores_comments_and_blank_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / '.env'
            env_path.write_text(
                '\n'.join([
                    '# demo env',
                    'export E2E_MODEL=',
                    'export E2E_API_KEY=   ',
                    'export E2E_AUTH_TOKEN = token-with-spaces',
                    'E2E_BASE_URL=https://example.invalid/base',
                    '',
                ]),
                encoding='utf-8',
            )

            secrets = load_demo_secrets(env_path)

            self.assertIsNone(secrets.model)
            self.assertIsNone(secrets.api_key)
            self.assertEqual(secrets.auth_token, 'token-with-spaces')
            self.assertEqual(secrets.base_url, 'https://example.invalid/base')

    def test_format_json_line_matches_demo_output_style(self) -> None:
        line = format_json_line('tool_use', {'toolName': 'Read', 'input': {'path': 'README.md'}})

        self.assertEqual(line, '[tool_use] {"toolName": "Read", "input": {"path": "README.md"}}')


class DemoSkillBootstrapTests(unittest.TestCase):
    def test_demo_skill_is_importable_from_repo_root_without_installed_package(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)

        result = subprocess.run(
            [
                sys.executable,
                '-c',
                "import runpy; runpy.run_path('demo/skill.py', run_name='not_main')",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f'stdout={result.stdout}\nstderr={result.stderr}',
        )


if __name__ == '__main__':
    unittest.main()
