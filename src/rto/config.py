"""Application configuration (12-factor: everything comes from environment variables).

All variables are prefixed with ``RTO_``. See ``docs/deployment.md`` for the full reference.
"""

from __future__ import annotations

import os
import pathlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RTO_", env_file=".env", extra="ignore")

    # --- runtime -------------------------------------------------------------------------------
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    base_url: str = "http://localhost:8000"

    # --- database ------------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/rto"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_echo: bool = False

    # --- sessions / security -------------------------------------------------------------------
    secret_key: str = Field(default="dev-insecure-secret-change-me", min_length=16)
    session_cookie_name: str = "rto_session"
    session_max_age_seconds: int = 8 * 3600
    session_https_only: bool = False

    # --- authentication ------------------------------------------------------------------------
    # "oidc" = Microsoft Entra ID (OpenID Connect authorization code flow)
    # "dev"  = local impersonation login page, NEVER allowed in production
    auth_mode: Literal["oidc", "dev"] = "dev"
    oidc_tenant_id: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_authority: str = "https://login.microsoftonline.com"
    # Claim used to match the signed-in user to an employee record (case-insensitive e-mail match).
    oidc_user_claim: str = "preferred_username"
    # Optional: Entra ID app roles (claim "roles") mapped to application roles.
    oidc_roles_claim: str = "roles"
    # Bearer-token API access (machine-to-machine / SPA). Audience is usually "api://<client-id>".
    api_audience: str = ""

    # --- workflow ------------------------------------------------------------------------------
    # Ordered approval steps for requests. Supported values:
    #   DIRECT_MANAGER, NEXT_LEVEL_MANAGER, PEOPLE_PARTNER
    approval_chain: list[str] = ["DIRECT_MANAGER", "PEOPLE_PARTNER"]
    dispute_window_days: int = 7

    # --- uploads -------------------------------------------------------------------------------
    max_upload_mb: int = 25

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.environment == "production":
            if self.auth_mode == "dev":
                raise ValueError("RTO_AUTH_MODE=dev is not allowed in production")
            if self.secret_key.startswith("dev-insecure"):
                raise ValueError("RTO_SECRET_KEY must be set in production")
            if not self.session_https_only:
                raise ValueError("RTO_SESSION_HTTPS_ONLY must be true in production")
        return self

    @property
    def oidc_metadata_url(self) -> str:
        return f"{self.oidc_authority}/{self.oidc_tenant_id}/v2.0/.well-known/openid-configuration"


def project_root() -> pathlib.Path:
    """Directory holding ``alembic.ini``, ``migrations/`` and ``sample_data/``.

    Source checkout: the repository root. Container: the working directory (``/app``).
    Override with ``RTO_HOME``.
    """
    if os.environ.get("RTO_HOME"):
        return pathlib.Path(os.environ["RTO_HOME"])
    repo = pathlib.Path(__file__).resolve().parents[2]
    return repo if (repo / "migrations").is_dir() else pathlib.Path.cwd()


@lru_cache
def get_settings() -> Settings:
    return Settings()
