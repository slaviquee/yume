"""Agent service configuration loaded from environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    anthropic_api_key: str = ""
    foreground_model: str = "claude-sonnet-4-6"
    foreground_effort: str = "medium"
    foreground_max_tokens: int = 8192
    worker_model: str = "claude-sonnet-4-6"
    max_concurrent_workers: int = 2
    default_worker_timeout_sec: int = 300
    worker_progress_interval_sec: int = 5
    hermes_bin: str = "hermes"
    hermes_mode: str = "auto"  # "auto" | "real" | "mock"
    history_max_turns: int = 20
    blocked_apps: tuple[str, ...] = field(
        default_factory=lambda: (
            "System Settings",
            "System Preferences",
            "Keychain Access",
            "Terminal",
            "iTerm",
            "iTerm2",
            "1Password",
            "Bitwarden",
            "Wallet",
        )
    )


def load_config() -> AgentConfig:
    return AgentConfig(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        foreground_model=os.environ.get("YUME_FOREGROUND_MODEL", "claude-sonnet-4-6"),
        foreground_effort=os.environ.get("YUME_FOREGROUND_EFFORT", "medium"),
        foreground_max_tokens=_env_int("YUME_FOREGROUND_MAX_TOKENS", 8192),
        worker_model=os.environ.get("YUME_WORKER_MODEL", "claude-sonnet-4-6"),
        hermes_bin=os.environ.get("HERMES_BIN", "hermes"),
        hermes_mode=os.environ.get("YUME_HERMES_MODE", "auto"),
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
