"""Logging utilities: sanitise secrets (JWT, RTSP creds, API keys) before emission."""

from __future__ import annotations

import logging
import re
from typing import Any

SECRET_PATTERNS = [
    # jwt tokens (compact)
    re.compile(r"\S+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # bearer auth
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    # generic key=value secrets
    re.compile(r"(api[_-]?key|password|secret|token)\s*[:=]\s*[A-Za-z0-9\-._~+/]+=*"),
]

DEFAULT_LOGGER = logging.getLogger("industrialvision")


def redact(value: Any, redact: bool = True) -> Any:
    """Recursively redact secrets from log records and returned payloads."""
    if not redact:
        return value
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    if isinstance(value, str):
        return _redact_value(value)
    return value


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        out = v
        for pat in SECRET_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        # UV/UVICORN style query components often carry tokens; keep only if it passed
        return out
    if isinstance(v, dict):
        return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_redact_value(x) for x in v]
    return v
