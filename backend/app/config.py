from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    cala_mcp_url: str | None = "https://api.cala.ai/mcp/"
    cala_api_key: str | None = None
    cala_mcp_token: str | None = None
    cala_transport: Literal["streamable_http", "sse"] = "streamable_http"
    cala_tool_mapping: Path | None = None
    cala_request_timeout_seconds: float = Field(default=15, gt=0, le=80)
    model_provider: Literal["codex", "openai_compatible", "disabled"] = "codex"
    codex_executable: str = "codex"
    model_base_url: str | None = None
    model_api_key: str | None = None
    model_name: str | None = None
    model_timeout_seconds: float = Field(default=20, gt=0, le=30)
    chat_timeout_seconds: float = Field(default=60, gt=0, le=120)
    chat_max_concurrency: int = Field(default=2, ge=1, le=8)

    lookback_months: int = Field(default=60, ge=1, le=120)
    preliminary_limit: int = Field(default=10, ge=1, le=10)
    profile_limit: int = Field(default=5, ge=1, le=5)
    cala_concurrency: int = Field(default=4, ge=1, le=4)
    cala_call_limit: int = Field(default=20, ge=1, le=100)
    run_deadline_seconds: float = Field(default=90, gt=0, le=90)
    assembly_reserve_seconds: float = Field(default=10, ge=0, le=10)
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    cache_dir: Path = BACKEND_DIR / ".cache"
    retention_seconds: int = Field(default=3600, ge=60)
    max_active_runs: int = Field(default=8, ge=1, le=100)
    max_stored_runs: int = Field(default=200, ge=1)
    frontend_origin: str = "http://localhost:3000"

    @model_validator(mode="after")
    def check_settings(self):
        if self.assembly_reserve_seconds >= self.run_deadline_seconds:
            raise ValueError("Assembly reserve must be shorter than the run deadline")
        if self.model_provider == "openai_compatible" and not all(
            [self.model_base_url, self.model_api_key, self.model_name]
        ):
            raise ValueError("OpenAI-compatible provider requires MODEL_BASE_URL, MODEL_API_KEY and MODEL_NAME")
        if self.cala_tool_mapping and not self.cala_tool_mapping.is_absolute():
            self.cala_tool_mapping = BACKEND_DIR / self.cala_tool_mapping
        return self
