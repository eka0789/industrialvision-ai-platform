"""Health & system endpoints."""

from __future__ import annotations

from contextlib import closing
from socket import socket, AF_INET, SOCK_STREAM

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.session import SessionLocal, engine

router = APIRouter(prefix="/health", tags=["system"])


def _db_ok() -> bool:
    try:
        with closing(SessionLocal()) as session:
            with session.connection():
                return True
    except Exception:
        return False


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket(AF_INET, SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            return True
    except Exception:
        return False


@router.get("", response_model=None)
def health():
    db_status = "ok" if _db_ok() else "degraded"
    return JSONResponse(
        content={
            "status": "ok" if (db_status == "ok" and _port_open(settings.HOST, settings.PORT)) else "degraded",
            "environment": settings.APP_ENV,
            "version": settings.VERSION,
            "database": db_status,
            "camera_connections": 0,
        },
        status_code=200,
    )
