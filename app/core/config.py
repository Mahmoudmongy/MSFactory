"""Application settings, loaded from environment variables.

Every value has a development-safe default so the app boots with zero
configuration, but production deployments are expected to override them
(notably SECRET_KEY, DATABASE_URL and the cookie/CORS settings).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MSFACTORY_", extra="ignore")

    app_name: str = "MS-Factory"
    environment: str = "development"
    debug: bool = False

    # Security
    secret_key: str = "dev-only-secret-change-in-production"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4

    # Database
    database_url: str = "postgresql+psycopg://msfactory:msfactory_dev_pw@127.0.0.1:5432/msfactory"
    # Kept well under the Supabase session-pooler limit (15 clients) so the
    # bootstrap fan-out plus concurrent users cannot exhaust the pooler.
    db_pool_size: int = 6
    db_max_overflow: int = 4
    # Supabase closes idle pooler sessions; recycle before it does.
    db_pool_recycle: int = 240
    db_echo: bool = False

    # Redis (rate limiting, caching, background jobs)
    redis_url: str = "redis://127.0.0.1:6379/0"

    # CORS
    # Comma-separated origins. ``*`` allows any, which browsers refuse to
    # combine with credentials; when the frontend is hosted separately (a
    # static host pointing here) list its origin explicitly.
    cors_origins: str = "*"

    # Rate limiting
    rate_limit_per_minute: int = 120
    login_rate_limit_per_minute: int = 10
    # Take the client address from CF-Connecting-IP / X-Forwarded-For instead of
    # the socket peer. Only correct when a trusted proxy sits in front: a client
    # that reaches the app directly can forge those headers and dodge the limit.
    trust_proxy_headers: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.is_production:
            if self.secret_key == "dev-only-secret-change-in-production":
                raise ValueError(
                    "MSFACTORY_SECRET_KEY must be set to a strong unique value in production"
                )
            if len(self.secret_key) < 32:
                raise ValueError("MSFACTORY_SECRET_KEY must be at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
