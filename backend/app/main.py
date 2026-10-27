"""Main FastAPI application factory."""

from __future__ import annotations

import contextlib

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers.admin import router as admin_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.cameras.camera import router as camera_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.inspections.inspection import router as inspections_router
from app.api.v1.routers.models import router as models_router
from app.api.v1.routers.websockets import router as websockets_router
from app.core.config import settings
from app.services.live_feed import live_feed


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.DEMO_LIVE_FEED and settings.APP_ENV.casefold() != "test":
        live_feed.start()
    try:
        yield
    finally:
        await live_feed.stop()


def create_app() -> FastAPI:
    """Application factory function."""
    _app = FastAPI(
        title=settings.PROJECT_NAME,
        description="IndustrialVision AI: Real-time defect detection & quality inspection",
        version=settings.VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
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
    _app.include_router(auth_router)
    _app.include_router(health_router)
    _app.include_router(camera_router)
    _app.include_router(admin_router)
    _app.include_router(inspections_router)
    _app.include_router(models_router)
    _app.include_router(websockets_router)

    @_app.get("/", tags=["root"])
    def root():
        return {
            "project": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "application_loaded",
            "endpoints": {
                "auth": "/auth/login",
                "health": "/health",
                "cameras": "/cameras",
                "inspections": "/inspections",
                "models": "/models",
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