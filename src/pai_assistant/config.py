"""Settings, loaded from environment variables (prefix ``PAI_``) or a ``.env`` file."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _xdg(var: str, fallback: str) -> Path:
    import os

    return Path(os.environ.get(var) or Path.home() / fallback)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAI_", env_file=".env", extra="ignore")

    # Any PydanticAI model string, e.g.
    #   anthropic:claude-sonnet-5-5 | openai:gpt-5.1 | google-gla:gemini-2.5-pro
    # For Ollama: set OLLAMA_BASE_URL and use ollama:<model>.
    model: str = "anthropic:claude-sonnet-5-5"

    user_name: str | None = None
    timezone: str = "Europe/Amsterdam"

    data_dir: Path = Field(default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "pai-assistant")
    config_dir: Path = Field(default_factory=lambda: _xdg("XDG_CONFIG_HOME", ".config") / "pai-assistant")

    # How many past user turns of the current conversation are sent to the model.
    history_turns: int = 30

    # Feature switches
    enable_google: bool = True
    enable_shell: bool = True
    enable_web: bool = True

    shell_timeout: int = 60

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
