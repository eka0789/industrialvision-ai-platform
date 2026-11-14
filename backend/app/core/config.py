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

    # Redis event bus / worker queue. Empty string = in-process bus (single-process
    # dev only; /health reports the active bus mode explicitly). Production must
    # configure redis:// or rediss:// so fan-out works across processes.
    REDIS_URL: str = ""

    # Background task transport: "in-process" (thread pool, dev default), "rq"
    # (Redis queue consumed by `python -m app.worker`), or "auto" (rq when
    # REDIS_URL is set). Production must use "rq" so the worker service exists.
    TASK_QUEUE_MODE: Literal["in-process", "rq", "auto"] = "in-process"
    TASK_MAX_RETRIES: int = Field(default=3, ge=0, le=10)
    TASK_RETRY_DELAY_SECONDS: float = Field(default=1.0, gt=0.0, le=60.0)

    MAX_UPLOAD_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    MAX_UPLOAD_VIDEO_BYTES: int = Field(default=5 * 1024 * 1024 * 1024, gt=0)
    MAX_IMAGE_PIXELS: int = Field(default=40_000_000, gt=0)
    MAX_INFLIGHT_FRAMES_PER_CAMERA: int = Field(default=64, gt=0)
    MAX_CAMERA_CONNECTIONS: int = Field(default=16, gt=0)

    EVIDENCE_ROOT: str = "data/evidence"
    SAMPLE_MEDIA_DIR: str = "data/samples"

    # Demo live feed: fabricates realistic inspections for simulated cameras and
    # broadcasts them over /ws/inspections/live. Refused at startup in production.
    DEMO_LIVE_FEED: bool = True
    DEMO_FEED_INTERVAL_SECONDS: float = Field(default=6.0, gt=0.0, le=3600.0)

    # Refresh tokens: long-lived per shift, rotated on every use, revocable.
    REFRESH_TOKEN_EXPIRE_HOURS: int = Field(default=12, ge=1, le=72)
    # Max failed logins per account within the lockout window before temporary lock.
    LOGIN_MAX_FAILURES: int = Field(default=5, ge=1, le=100)
    LOGIN_LOCKOUT_MINUTES: int = Field(default=15, ge=1, le=1440)
    # Set true when TLS terminates in front of the app (honours X-Forwarded-Proto).
    TRUST_PROXY_HEADERS: bool = False

    # Session token storage for the SPA: "body" returns tokens in JSON bodies
    # (API clients, current default). "cookie" sets httpOnly cookies instead and
    # enforces double-submit CSRF on unsafe methods.
    AUTH_TOKEN_STORAGE: Literal["body", "cookie"] = "body"
    # None = auto (secure cookies in production, relaxed in dev). Set explicitly
    # when running production-behind-TLS on plain HTTP internally.
    AUTH_COOKIE_SECURE: bool | None = None

    MODEL_ROOT: str = "models"

    # Predictive maintenance (Phase B): path to an optional real AI4I-format
    # CSV; when absent the documented synthetic mirror is used. Sensor history
    # older than the retention window is pruned by a background task.
    PDM_DATASET_CSV: str = "data/pdm/ai4i2020.csv"
    PDM_SENSOR_RETENTION_DAYS: int = Field(default=180, ge=1, le=3650)

    # Knowledge AI / RAG (Phase C). Empty LLM keys keep the labeled mock-demo
    # answerer; embeddings fall back to the documented local hashing scheme.
    RAG_EMBEDDING_PROVIDER: str = "auto"  # auto | local-hashing | openai | sentence-transformers
    RAG_LLM_PROVIDER: str = "auto"  # auto | mock-demo | openai | gemini
    RAG_CHUNK_SIZE: int = Field(default=800, ge=100, le=4000)
    RAG_CHUNK_OVERLAP: int = Field(default=100, ge=0, le=1000)
    RAG_MAX_UPLOAD_BYTES: int = Field(default=25 * 1024 * 1024, gt=0)
    RAG_TOP_K: int = Field(default=5, ge=1, le=20)
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    # Optional MLflow experiment tracking mirror (E3); local run dir stays the
    # source of truth. Empty = disabled.
    MLFLOW_TRACKING_URI: str = ""
    # Comma-separated run directories scanned read-only by the experiment
    # browser (G2). Only manifests are read — artifacts stay on disk.
    TRAINING_RUNS_DIRS: str = "training_runs,data/pdm/training_runs,data/anomaly_runs"
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
            if self.DEMO_LIVE_FEED:
                raise ValueError("DEMO_LIVE_FEED must be false in production")
            if self.LOG_LEVEL.upper() == "DEBUG":
                raise ValueError("LOG_LEVEL=DEBUG is not allowed in production (may log sensitive data)")
            if not self.REDIS_URL.strip():
                raise ValueError("Production requires REDIS_URL (redis:// or rediss://) for cross-process fan-out and the worker queue")
            if not self.REDIS_URL.strip().startswith(("redis://", "rediss://")):
                raise ValueError("REDIS_URL must start with redis:// or rediss://")
            if self.TASK_QUEUE_MODE != "rq":
                raise ValueError("TASK_QUEUE_MODE must be 'rq' in production (standalone worker service)")

        return self


settings = Settings()
