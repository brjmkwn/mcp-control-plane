"""Configuration management using Pydantic Settings with fail-closed defaults."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientPrincipal(BaseSettings):
    """Authenticated client identity and permissions."""

    client_id: str
    scopes: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    """Production gateway settings with fail-closed defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Transport & Security
    # Fail-closed: empty allowlist blocks any incoming Origin header
    mcp_allowed_origins: list[str] = Field(
        default_factory=list,
        validation_alias="MCP_ALLOWED_ORIGINS",
    )

    # Scoped Bearer Token configuration
    # Mapping of raw bearer token -> {client_id: str, scopes: list[str]}
    mcp_tokens_json: str = Field(
        default="{}",
        validation_alias="MCP_TOKENS_JSON",
    )

    # Secret key for HMAC-SHA256 2-step confirmation tokens
    mcp_confirmation_secret: str = Field(
        default="default-test-secret-key-32-chars-minimum!!",
        validation_alias="MCP_CONFIRMATION_SECRET",
    )

    # TTL for confirmation tokens in seconds
    confirmation_ttl_seconds: int = Field(default=120)

    # Storage & Backing Services
    database_url: str = Field(
        default="",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="",
        validation_alias="REDIS_URL",
    )

    # GitHub Triage Provider
    github_provider_mode: str = Field(
        default="mock",
        validation_alias="GITHUB_PROVIDER_MODE",
    )
    github_token: str = Field(
        default="",
        validation_alias="GITHUB_TOKEN",
    )

    # Observability
    prometheus_url: str = Field(
        default="http://localhost:9090",
        validation_alias="PROMETHEUS_URL",
    )

    # Server binding
    mcp_host: str = Field(default="0.0.0.0", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8000, validation_alias="MCP_PORT")

    # Explicit test/CI mode toggle (enables in-memory SQLite and mock fallbacks)
    test_mode: bool = Field(default=False, validation_alias="MCP_TEST_MODE")

    @field_validator("mcp_allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, list):
            return [str(o).strip() for o in v if str(o).strip()]
        return []

    def get_token_map(self) -> dict[str, ClientPrincipal]:
        """Parse mcp_tokens_json into a mapping of bearer token to ClientPrincipal."""
        try:
            raw_map = json.loads(self.mcp_tokens_json)
            if not isinstance(raw_map, dict):
                return {}
            token_map: dict[str, ClientPrincipal] = {}
            for token, meta in raw_map.items():
                if isinstance(meta, dict) and "client_id" in meta:
                    token_map[token] = ClientPrincipal(
                        client_id=str(meta["client_id"]),
                        scopes=[str(s) for s in meta.get("scopes", [])],
                    )
            return token_map
        except Exception:
            return {}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Retrieve singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(new_settings: Settings) -> None:
    """Override singleton Settings instance (useful for test fixtures)."""
    global _settings
    _settings = new_settings
