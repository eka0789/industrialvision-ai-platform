"""Main FastAPI application factory."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.cameras.camera import router as camera_router

from app.core.config import settings

# Try to import optional routers (will fail gracefully if missing)
try:
    from app.api.v1.routers.inspections.inspection import router as inspection_router  # type: ignore
except Exception:  # pragma: no cover
    inspection_router = None

try:
    from app.api.v1.routers.detections.detector import router as detector_router  # type: ignore
except Exception:  # pragma: no cover
    detector_router = None

try:
    from app.api.v1.routers.models.model import router as model_router  # type: ignore
except Exception:  # pragma: no cover
    model_router = None

try:
    from app.api.v1.routers.evaluations.eval import router as eval_router  # type: ignore
except Exception:  # pragma: no cover
    eval_router = None

try:
    from app.api.v1.routers.mlflow.router import router as mlflow_router  # type: ignore
except Exception:  # pragma: no cover
    mlflow_router = None

try:
    from app.api.v1.routers.anomalies.anomaly import router as anomaly_router  # type: ignore
except Exception:  # pragma: no cover
    anomaly_router = None

try:
    from app.api.v1.routers.admin.admin import router as admin_router  # type: ignore
except Exception:  # pragma: no cover
    admin_router = None

try:
    from app.api.v1.routers.files.upload import router as files_router  # type: ignore
except Exception:  # pragma: no cover
    files_router = None


def create_app() -> FastAPI:
    """Application factory function."""
    _app = FastAPI(
        title=settings.PROJECT_NAME,
        description="IndustrialVision AI: Real-time defect detection & quality inspection",
        version=settings.VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    _app.include_router(auth_router, prefix="/auth", tags=["auth"])

    _app.include_router(health_router, prefix="/health", tags=["system"])

    _app.include_router(camera_router, prefix="/cameras", tags=["cameras"])

    if inspection_router:
        _app.include_router(inspection_router, prefix="/inspections", tags=["inspections"])
    if detector_router:
        _app.include_router(detector_router, prefix="/detections", tags=["detections"])
    if model_router:
        _app.include_router(model_router, prefix="/models", tags=["models"])
    if eval_router:
        _app.include_router(eval_router, prefix="/evaluations", tags=["evaluations"])
    if mlflow_router:
        _app.include_router(mlflow_router, prefix="/mlflow", tags=["mlflow"])
    if anomaly_router:
        _app.include_router(anomaly_router, prefix="/anomalies", tags=["anomalies"])
    if admin_router:
        _app.include_router(admin_router, prefix="/admin", tags=["admin"])

    if files_router:
        _app.include_router(files_router, prefix="/files", tags=["files"])

    @_app.get("/", tags=["root"])
    def root():
        return {
            "project": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "operational",
            "endpoints": {
                "auth": "/auth/login",
                "health": "/health/",
                "cameras": "/cameras/",
            },
        }

    return _app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )