from __future__ import annotations

from typing import Dict

from ._exec import ClaudeCodeExec
from ._options import ClaudeCodeOptions, SessionOptions
from ._session import Session


class ClaudeCode:
    def __init__(self, options: ClaudeCodeOptions = None) -> None:
        opts = options or ClaudeCodeOptions()
        normalized_env = merge_claude_env(opts)
        self._options = ClaudeCodeOptions(
            cli_path=opts.cli_path,
            env=normalized_env,
            api_key=opts.api_key,
            auth_token=opts.auth_token,
            base_url=opts.base_url,
        )
        self._exec = ClaudeCodeExec(opts.cli_path, normalized_env)

    def start_session(self, options: SessionOptions = None) -> Session:
        return Session(self._exec, self._options, options or SessionOptions())

    def resume_session(self, session_id: str, options: SessionOptions = None) -> Session:
        return Session(self._exec, self._options, options or SessionOptions(), session_id)

    def continue_session(self, options: SessionOptions = None) -> Session:
        return Session(self._exec, self._options, options or SessionOptions(), None, True)


def merge_claude_env(options: ClaudeCodeOptions) -> Dict[str, str]:
    env = dict(options.env or {})
    if options.api_key is not None:
        env["ANTHROPIC_API_KEY"] = options.api_key
    if options.auth_token is not None:
        env["ANTHROPIC_AUTH_TOKEN"] = options.auth_token
    if options.base_url is not None:
        env["ANTHROPIC_BASE_URL"] = options.base_url
    return env
