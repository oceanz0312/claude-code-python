from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from claude_code import ClaudeCodeOptions, SessionOptions


AuthMode = str


@dataclass
class E2ESecrets:
    model: Optional[str] = None
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    base_url: Optional[str] = None


@dataclass
class E2EConfig:
    repo_root: str
    artifact_root: str
    secrets: E2ESecrets
    model: str
    default_session_options: SessionOptions


_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_ARTIFACT_ROOT = str(Path(__file__).resolve().parent / "artifacts")
_cached_config: Optional[E2EConfig] = None


def load_e2e_config() -> E2EConfig:
    global _cached_config
    if _cached_config is None:
        _cached_config = _load_config_internal()
    return _cached_config


def get_client_options(secrets: E2ESecrets, auth_mode: AuthMode) -> ClaudeCodeOptions:
    if auth_mode == "api-key":
        if not secrets.api_key:
            raise RuntimeError("E2E requires E2E_API_KEY env var for api-key cases.")
        return ClaudeCodeOptions(api_key=secrets.api_key)

    if not secrets.auth_token or not secrets.base_url:
        raise RuntimeError(
            "E2E requires both E2E_AUTH_TOKEN and E2E_BASE_URL env vars for auth-token cases."
        )
    return ClaudeCodeOptions(auth_token=secrets.auth_token, base_url=secrets.base_url)


def list_available_auth_modes() -> list[AuthMode]:
    config = load_e2e_config()
    modes: list[AuthMode] = []
    if config.secrets.api_key:
        modes.append("api-key")
    if config.secrets.auth_token and config.secrets.base_url:
        modes.append("auth-token")
    return modes


def _load_config_internal() -> E2EConfig:
    secrets = _load_secrets_from_env()
    return E2EConfig(
        repo_root=_REPO_ROOT,
        artifact_root=_ARTIFACT_ROOT,
        secrets=secrets,
        model=(secrets.model or "").strip() or "sonnet",
        default_session_options=SessionOptions(
            bare=True,
            setting_sources="",
            verbose=True,
            include_partial_messages=True,
            dangerously_skip_permissions=True,
        ),
    )


def _load_secrets_from_env() -> E2ESecrets:
    auth_token = _get_optional_string(os.environ.get("E2E_AUTH_TOKEN"))
    base_url = _get_optional_string(os.environ.get("E2E_BASE_URL"))
    api_key = _get_optional_string(os.environ.get("E2E_API_KEY"))
    model = _get_optional_string(os.environ.get("E2E_MODEL"))

    if not auth_token and not api_key:
        raise RuntimeError(
            "No E2E_AUTH_TOKEN or E2E_API_KEY env var found. "
            "Copy .env.example to .env, fill in values, and load it before running tests."
        )

    return E2ESecrets(model=model, api_key=api_key, auth_token=auth_token, base_url=base_url)


def _get_optional_string(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None
