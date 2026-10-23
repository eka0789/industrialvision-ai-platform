"""Settings for IndustrialVision AI."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_NAME: str = "IndustrialVision AI"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ACCESS_LOG: bool = True

    # Database
    DATABASE_URL: str = "sqlite:///./industrialvision_ai.db"

    # Secrets (never commit secrets; generate with `openssl rand -hex 32`)
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    # CORS - explicit, do not use "*" in production
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Work limits
    MAX_UPLOAD_BYTES: int = 104_857_600  # 100 MB
    MAX_UPLOAD_VIDEO_BYTES: int = 5 * 1024 * 1024 * 1024  # 5 GB
    MAX_INFLIGHT_FRAMES_PER_CAMERA: int = 64

    # Evidence storage
    EVIDENCE_ROOT: str = "data/evidence"

    # Camera
    MAX_CAMERA_CONNECTIONS: int = 16

    # Demo / sample assets
    SAMPLE_MEDIA_DIR: str = "data/samples"

    # Anomaly detection threshold (score 0-1 mapped by model)
    DEFAULT_ANOMALY_SCORE_THRESHOLD: float = 0.5

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
