"""Settings, loaded from environment variables (prefix ``PAI_``) or ``.env`` files.

``.env`` files are read from the current directory and from ``~/.config/pai-assistant/.env``
(the current directory wins). They may also hold provider keys such as ``OPENROUTER_API_KEY``.
"""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "pai-assistant"
ENV_FILES = (Path(".env"), CONFIG_DIR / ".env")


def load_env() -> None:
    """Export ``.env`` entries into the process environment.

    pydantic-settings only reads the ``PAI_*`` fields, but the model providers read their API keys
    (``ANTHROPIC_API_KEY``, ``OPENROUTER_API_KEY``, ...) straight from ``os.environ``.
    Real environment variables take precedence over ``.env`` files.
    """
    for path in ENV_FILES:
        if path.is_file():
            load_dotenv(path, override=False)
    # OpenRouter app attribution (shows up on openrouter.ai/activity).
    os.environ.setdefault("OPENROUTER_APP_TITLE", "pai-assistant")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAI_", env_file=ENV_FILES[::-1], extra="ignore")

    # Model to use. Two modes:
    #
    # 1. PAI_BASE_URL unset: a PydanticAI model string ("<provider>:<model>"), e.g.
    #      openrouter:deepseek/deepseek-v4.1-flash-1017 | anthropic:claude-sonnet-5-5
    #      openai:gpt-5.1 | google-gla:gemini-2.5-pro | ollama:qwen3:14b
    #    The provider's API key comes from its usual env var (OPENROUTER_API_KEY, ...).
    #
    # 2. PAI_BASE_URL set: any OpenAI-compatible endpoint (DeepSeek, vLLM, llama.cpp, LM Studio,
    #    LiteLLM, Together, Groq, ...). PAI_MODEL is then the endpoint's own model name, and the key
    #    comes from PAI_API_KEY (optional for local servers).
    model: str = "openrouter:deepseek/deepseek-v4.1-flash-1017"
    base_url: str | None = None
    api_key: str | None = None

    user_name: str | None = None
    timezone: str = "Europe/Amsterdam"

    data_dir: Path = Field(default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "pai-assistant")
    config_dir: Path = CONFIG_DIR

    # How many past user turns of the current conversation are sent to the model.
    history_turns: int = 30

    # Feature switches
    enable_google: bool = True
    enable_shell: bool = True
    enable_web: bool = True

    shell_timeout: int = 60

    @property
    def model_label(self) -> str:
        return f"{self.model} @ {self.base_url}" if self.base_url else self.model

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"

    @property
    def google_credentials_path(self) -> Path:
        """OAuth client secrets downloaded from Google Cloud Console."""
        return self.config_dir / "google_credentials.json"

    @property
    def google_token_path(self) -> Path:
        return self.data_dir / "google_token.json"

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
