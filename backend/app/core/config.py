"""Single source of validated application configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    PROJECT_NAME: str = "IndustrialVision AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    APP_ENV: str = "development"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = Field(default=8000, ge=1, le=65535)
    ACCESS_LOG: bool = True
    LOG_LEVEL: str = "INFO"

    JWT_SECRET: str = "dev-only-insecure-secret-change-before-production-32"
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=1, le=1440)

    DATABASE_URL: str = "sqlite:///./industrialvision_ai.db"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    MAX_UPLOAD_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    MAX_UPLOAD_VIDEO_BYTES: int = Field(default=5 * 1024 * 1024 * 1024, gt=0)
    MAX_IMAGE_PIXELS: int = Field(default=40_000_000, gt=0)
    MAX_INFLIGHT_FRAMES_PER_CAMERA: int = Field(default=64, gt=0)
    MAX_CAMERA_CONNECTIONS: int = Field(default=16, gt=0)

    EVIDENCE_ROOT: str = "data/evidence"
    SAMPLE_MEDIA_DIR: str = "data/samples"

    MODEL_ROOT: str = "models"
    DEFAULT_ONNX_MODEL: str = "model.onnx"
    ONNX_DEVICE: Literal["cpu", "cuda"] = "cpu"
    CONF_THRESHOLD: float = Field(default=0.25, ge=0.0, le=1.0)
    IOU_THRESHOLD: float = Field(default=0.45, ge=0.0, le=1.0)
    IMAGE_SIZE: int = Field(default=640, ge=32, le=4096)
    FPS_INPUT: float = Field(default=25.0, gt=0.0, le=240.0)
    DEFAULT_ANOMALY_SCORE_THRESHOLD: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def cors_origins(self) -> list[str]:
        """Return normalized origins for Starlette's CORS middleware."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> Settings:
        try:
            url = make_url(self.DATABASE_URL)
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if url.drivername.endswith(("+aiosqlite", "+asyncpg")):
            raise ValueError("DATABASE_URL must use a synchronous SQLAlchemy driver")
        if url.get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("DATABASE_URL backend must be SQLite or PostgreSQL")

        if self.APP_ENV.casefold() == "production":
            insecure_prefixes = ("dev-", "change-me", "replace-with", "your-")
            if len(self.JWT_SECRET) < 32 or self.JWT_SECRET.casefold().startswith(insecure_prefixes):
                raise ValueError("Production requires a unique JWT_SECRET of at least 32 characters")
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origins are not allowed in production")
            if url.get_backend_name() != "postgresql":
                raise ValueError("Production DATABASE_URL must use PostgreSQL")

        return self


settings = Settings()
