from __future__ import annotations

import json
import readline
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent
ROOT_DIR = DEMO_DIR.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from claude_code import ClaudeCode, ClaudeCodeOptions, SessionOptions


@dataclass
class DemoSecrets:
    model: str | None = None
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None


def load_demo_secrets(env_path: str | Path | None = None) -> DemoSecrets:
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parent / '.env'
    values: dict[str, str] = {}
    if path.exists():
        for raw_line in path.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[len('export '):].strip()
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if value[:1] == value[-1:] and value[:1] in {'"', "'"} and value:
                value = value[1:-1]
            values[key] = value
    return DemoSecrets(
        model=_optional_string(values.get('E2E_MODEL')),
        api_key=_optional_string(values.get('E2E_API_KEY')),
        auth_token=_optional_string(values.get('E2E_AUTH_TOKEN')),
        base_url=_optional_string(values.get('E2E_BASE_URL')),
    )


def _optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def build_client_options(secrets: DemoSecrets) -> ClaudeCodeOptions:
    return ClaudeCodeOptions(
        api_key=secrets.api_key,
        auth_token=secrets.auth_token,
        base_url=secrets.base_url,
    )


def build_session_options(repo_root: str | Path) -> SessionOptions:
    root = Path(repo_root)
    return SessionOptions(
        cwd=str(root),
        dangerously_skip_permissions=True,
        plugin_dir=str(root / 'claude-code-plugin'),
    )


def write_line(line: str = '') -> None:
    print(line)


def ask(prompt: str) -> str:
    return input(prompt)


def format_json_line(label: str, value: Any) -> str:
    return f'[{label}] {json.dumps(value, ensure_ascii=False)}'


async def run_turn(session: Any, user_input: str) -> None:
    write_line()
    write_line('Claude:')
    streamed = await session.run_streamed(user_input)
    events = streamed.events

    streaming_mode: str | None = None

    def flush_streaming_line() -> None:
        nonlocal streaming_mode
        if streaming_mode is not None:
            write_line()
            streaming_mode = None

    async for event in events:
        if event.type == 'text_delta':
            if streaming_mode != 'text_delta':
                flush_streaming_line()
                print('[text_delta] ', end='')
                streaming_mode = 'text_delta'
            print(event.content, end='')
            continue
        if event.type == 'thinking_delta':
            if streaming_mode != 'thinking_delta':
                flush_streaming_line()
                print('[thinking_delta] ', end='')
                streaming_mode = 'thinking_delta'
            print(event.content, end='')
            continue
        if event.type == 'tool_use':
            flush_streaming_line()
            write_line(format_json_line('tool_use', {
                'toolUseId': event.tool_use_id,
                'toolName': event.tool_name,
                'input': event.input,
            }))
            continue
        if event.type == 'tool_result':
            flush_streaming_line()
            write_line(format_json_line('tool_result', {
                'toolUseId': event.tool_use_id,
                'output': event.output,
                'isError': event.is_error,
            }))
            continue
        if event.type == 'session_meta':
            flush_streaming_line()
            write_line(format_json_line('session_meta', {'model': event.model}))
            continue
        if event.type == 'turn_complete':
            flush_streaming_line()
            write_line(format_json_line('turn_complete', {
                'sessionId': event.session_id,
                'costUsd': event.cost_usd,
                'inputTokens': event.input_tokens,
                'outputTokens': event.output_tokens,
                'contextWindow': event.context_window,
            }))
            continue
        if event.type == 'error':
            flush_streaming_line()
            write_line(format_json_line('error', {
                'message': event.message,
                'sessionId': event.session_id,
            }))
    flush_streaming_line()
    write_line()


async def main() -> None:
    repo_root = ROOT_DIR
    secrets = load_demo_secrets(repo_root / '.env')

    claude = ClaudeCode(build_client_options(secrets))
    session = claude.start_session(build_session_options(DEMO_DIR))

    initial_prompt = '使用 ai-friendly-evaluate 技能评估当前仓库的 ai-friendly 分数'

    write_line('多轮 skill demo（输入 exit 退出）')
    write_line()

    is_first = True
    while True:
        user_input = initial_prompt if is_first else ask('\n你: ')
        if user_input == 'exit':
            break
        if is_first:
            write_line(f'你: {user_input}')
            is_first = False
        await run_turn(session, user_input)

    write_line()
    write_line('--- 完成 ---')
    write_line(f'Session ID: {session.id}')


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
