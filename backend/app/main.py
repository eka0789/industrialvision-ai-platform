"""Main FastAPI application factory."""

from __future__ import annotations

import contextlib
import time

import uvicorn
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api.v1.routers.admin import router as admin_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.cameras.camera import router as camera_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.inspections.inspection import router as inspections_router
from app.api.v1.routers.models import router as models_router
from app.api.v1.routers.pdm import router as pdm_router
from app.api.v1.routers.rag import router as rag_router
from app.api.v1.routers.agent import router as agent_router
from app.api.v1.routers.websockets import router as websockets_router
from app.api.v1.routers.streaming import router as streaming_router
from app.api.v1.routers.reports import router as reports_router
from app.api.v1.routers.training import router as training_router
from app.core.config import settings
from app.services.event_bus import event_bus
from app.services.live_feed import live_feed
from app.services.metrics import render_metrics
from app.services import tasks as _registered_tasks  # noqa: F401  (task registration side effect)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request rate and latency per normalized endpoint template."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            from app.services.metrics import observe_http

            path = request.scope["route"].path if "route" in request.scope else request.url.path
            observe_http(path, request.method, status_code, duration)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF enforcement for cookie session mode only.

    In body mode (default) this middleware is a no-op — bearer tokens are not
    ambient credentials, so CSRF does not apply. In cookie mode every unsafe
    method must present ``X-CSRF-Token`` matching the ``iv_csrf`` cookie,
    except the pre-session endpoints listed in CSRF_EXEMPT_PATHS (login /
    refresh establish the pair; health/metrics are read-only).
    """

    UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):
        from app.services.session_cookies import CSRF_EXEMPT_PATHS, cookie_mode, verify_csrf

        if (
            cookie_mode()
            and request.method.upper() in self.UNSAFE_METHODS
            and request.url.path not in CSRF_EXEMPT_PATHS
        ):
            try:
                verify_csrf(request)
            except Exception as exc:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=getattr(exc, "status_code", 403),
                    content={"detail": getattr(exc, "detail", "CSRF verification failed")},
                )
        return await call_next(request)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    await event_bus.startup()
    if settings.DEMO_LIVE_FEED and settings.APP_ENV.casefold() != "test":
        live_feed.start()
    try:
        yield
    finally:
        await live_feed.stop()
        await event_bus.shutdown()


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

    # Middleware stack: metrics (outermost) -> CORS -> CSRF (no-op in body mode).
    _app.add_middleware(MetricsMiddleware)
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _app.add_middleware(CsrfMiddleware)

    # Prometheus exposition. Unauthenticated by design: only reachable on the
    # internal Docker network (nginx does not proxy /metrics).
    @_app.get("/metrics", include_in_schema=False, tags=["system"])
    def metrics() -> Response:
        return Response(content=render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")

    # Include routers
    _app.include_router(auth_router)
    _app.include_router(health_router)
    _app.include_router(camera_router)
    _app.include_router(admin_router)
    _app.include_router(inspections_router)
    _app.include_router(models_router)
    _app.include_router(pdm_router)
    _app.include_router(rag_router)
    _app.include_router(agent_router)
    _app.include_router(websockets_router)
    _app.include_router(streaming_router)
    _app.include_router(reports_router)
    _app.include_router(training_router)

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