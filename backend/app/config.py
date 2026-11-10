"""Factory settings, read from environment variables or a `.env` file.

Secrets (Claude API key, later GitHub token and Azure Reader credentials) live
only on the factory VM. They are never committed and never passed to apps.
"""

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="FACTORY_",
        extra="ignore",
    )

    # Where the factory keeps its database and agent workspaces.
    data_dir: Path = Path.home() / ".software-factory"

    # Concurrency and limits (raise these when the VM is upgraded).
    max_concurrent_agents: int = 3

    # Optional OS-level bash sandbox for agents (Linux needs bubblewrap).
    bash_sandbox: bool = False

    # Origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Read without the FACTORY_ prefix because the Claude Agent SDK uses it too.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @cached_property
    def db_path(self) -> Path:
        return self.data_dir / "factory.db"

    @cached_property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @cached_property
    def plugin_dir(self) -> Path:
        """Factory Claude Code plugin (skills, later agents) in this repo."""
        return Path(__file__).resolve().parents[2] / "factory-plugin"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
