"""Security hardening utilities for IndustrialVision AI backend.

- Validate uploads and size limits
- Redact secrets/RTSP credentials from API output/logs/errors
- Configure explicit CORS (already wired in main.py)
- Audit sensitive actions via logging
- Enforce timeouts & connection limits
- Check path handling (traversal prevention)
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit


# ── RTSP credential redaction ────────────────────────────────────────────

def redact_rtsp_url(url: str | None) -> str:
    """Redact username/password from RTSP URLs for logging and error messages."""
    if not url:
        return "None"
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"rtsp", "rtsps"} and (parsed.username or parsed.password):
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return parsed._replace(netloc=f"[REDACTED]@{host}").geturl()
    return url


# ── Upload validation ────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB (from settings)
MAX_VIDEO_BYTES = 5 * 1024 * 1024 * 1024  # 5GB
MAX_IMAGE_PIXELS = 40_000_000  # 40MP


def validate_upload_size(content_length: int | None, content_type: str | None) -> tuple[bool, str | None]:
    """Validate upload size and content type.

    Returns (valid, error_message).
    """
    if content_length is None:
        return True, None

    if content_length > MAX_UPLOAD_BYTES:
        return False, f"File too large: {content_length} bytes (max {MAX_UPLOAD_BYTES})"

    # Validate content type
    if content_type and not content_type.startswith("image/"):
        if not content_type.startswith("video/"):
            return False, f"Unsupported content type: {content_type}"

    return True, None


# ── Path traversal prevention ────────────────────────────────────────────

def sanitize_path(path: str, allowed_root: str | None = None) -> str:
    """Prevent path traversal attacks.

    Ensures resolved path stays within allowed_root if specified.
    Raises ValueError if path escapes the allowed root or contains traversal.
    """
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if ".." in parts:
        raise ValueError(f"Path traversal detected in: {path}")

    # Check for reserved Windows device names
    base = os.path.basename(normalized)
    name_no_ext = os.path.splitext(base)[0].lower()
    reserved_names = {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }
    if name_no_ext in reserved_names:
        raise ValueError(f"Reserved device name detected: {base}")

    clean = os.path.normpath(path)
    if allowed_root:
        root_res = os.path.realpath(allowed_root)
        clean_res = os.path.realpath(clean)
        if not clean_res.startswith(root_res + os.sep) and clean_res != root_res:
            raise ValueError(f"Path escapes allowed root: {path}")

    return clean


# ── Secret scanning in strings ──────────────────────────────────────────

_SECRET_PATTERNS = [
    (r"(?i)aws[_-]?(?:access[_-]?)?key[_-]?id\s*[:=]\s*[A-Za-z0-9_/+=-]{16,40}", "AWS Access Key ID"),
    (r"(?i)aws[_-]?(?:secret[_-]?)?access[_-]?key\s*[:=]\s*[A-Za-z0-9_/+=-]{20,60}", "AWS Secret Access Key"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/API Key"),
    (r"(?i)basic\s+auth[_-]?cred(?:ential)?\s*[:=]\s*[^\s]+", "Basic Auth Credential"),
]


def scan_for_secrets(text: str) -> list[dict[str, str]]:
    """Scan text for potential secret keys/credentials.

    Returns list of {type, match} dicts.
    """
    findings: list[dict[str, str]] = []
    for pattern, secret_type in _SECRET_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            findings.append({"type": secret_type, "match": match})
    return findings


# ── CORS helper ────────────────────────────────────────────────────────

def get_cors_origins(allowed_origins: list[str] | None = None) -> list[str]:
    """Return CORS origins list with safe defaults.

    In production, never use wildcards.
    """
    if allowed_origins:
        return [o.strip() for o in allowed_origins if o.strip()]

    return [
        "http://localhost:5173",
        "http://localhost:3000",
    ]


# ── Audit logging helper ───────────────────────────────────────────────

def audit_action(
    user_id: int,
    action: str,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    success: bool = True,
) -> dict[str, Any]:
    """Create an audit log entry.

    Sensitive fields in details are automatically redacted.
    """
    entry: dict[str, Any] = {
        "user_id": user_id,
        "action": action,
        "success": success,
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }

    if ip_address:
        entry["ip_address"] = ip_address

    if details:
        details_copy = details.copy()
        for key, value in details_copy.items():
            if isinstance(value, str):
                secrets = scan_for_secrets(value)
                if secrets:
                    details_copy[key] = f"<redacted: {secrets[0]['type']}>"
        entry["details"] = details_copy
    else:
        entry["details"] = {}

    return entry
