"""Application settings using pydantic-settings."""

from __future__ import annotations

import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="IV_")

    PROJECT_NAME: str = "IndustrialVision AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    JWT_SECRET: str = "-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    DATABASE_URL: str = "sqlite+aiosqlite:///./industrialvision_ai.db"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


settings = Settings()