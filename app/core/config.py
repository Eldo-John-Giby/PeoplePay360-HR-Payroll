"""Application settings loaded from environment variables (pydantic-settings).

Everything here reads from env vars / .env so the same code runs locally,
in docker compose, and in CI. Import the module-level `settings` singleton.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+psycopg2://peoplepay:peoplepay@localhost:5432/peoplepay"
    )

    # --- Auth / JWT ---
    JWT_SECRET_KEY: str = "dev-secret-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # --- App ---
    APP_NAME: str = "PeoplePay360"
    API_V1_PREFIX: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()