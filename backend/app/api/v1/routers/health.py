"""Health & system endpoints."""

from __future__ import annotations

import threading
from contextlib import closing
from socket import socket, AF_INET, SOCK_STREAM

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import SessionLocal, engine
from app.core.logging_utils import redact

router = APIRouter(prefix="/health", tags=["system"])


def _db_ok() -> bool:
    try:
        with closing(SessionLocal()).connection():
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
        content=redact(
            {
                "status": "ok" if (db_status == "ok" and _port_open(settings.HOST, settings.PORT)) else "degraded",
                "environment": settings.APP_ENV,
                "version": "0.1.0-mvp",
                "database": db_status,
                "camera_connections": 0,
            }
        ),
        status_code=200,
    )
