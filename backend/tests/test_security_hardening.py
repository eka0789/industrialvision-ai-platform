"""Tests for T32 Security Hardening.

Covers:
- Upload size and content type validation
- RTSP credentials redaction from URLs and error strings
- Path traversal prevention and Windows reserved device name detection
- Secret scanning (AWS keys, OpenAI tokens, basic auth)
- CORS origin resolution
- Audit logging with automated secret redaction
"""

from __future__ import annotations

import unittest
from app.security.hardening import (
    redact_rtsp_url,
    validate_upload_size,
    sanitize_path,
    scan_for_secrets,
    get_cors_origins,
    audit_action,
)


class SecurityHardeningTests(unittest.TestCase):
    def test_rtsp_redaction(self):
        url = "rtsp://admin:supersecret123@10.0.0.5:554/live/ch0"
        redacted = redact_rtsp_url(url)
        self.assertNotIn("supersecret123", redacted)
        self.assertIn("[REDACTED]@10.0.0.5:554", redacted)

        # None or non-rtsp returns as is or 'None'
        self.assertEqual(redact_rtsp_url(None), "None")
        self.assertEqual(redact_rtsp_url("http://example.com"), "http://example.com")

    def test_validate_upload_size(self):
        # Normal image under limit
        valid, err = validate_upload_size(5 * 1024 * 1024, "image/jpeg")
        self.assertTrue(valid)
        self.assertIsNone(err)

        # Exceeding 100MB limit
        valid, err = validate_upload_size(150 * 1024 * 1024, "image/png")
        self.assertFalse(valid)
        self.assertIn("File too large", err)

        # Unsupported content type (e.g. executable or script)
        valid, err = validate_upload_size(1024, "application/x-executable")
        self.assertFalse(valid)
        self.assertIn("Unsupported content type", err)

        # Video content type is permitted for video endpoints
        valid, err = validate_upload_size(1024 * 1024, "video/mp4")
        self.assertTrue(valid)

    def test_sanitize_path_traversal_prevention(self):
        # Traversal attempts should raise ValueError
        with self.assertRaises(ValueError):
            sanitize_path("../../etc/passwd")

        with self.assertRaises(ValueError):
            sanitize_path("foo/../../../bar")

        # Windows reserved names should raise ValueError
        with self.assertRaises(ValueError):
            sanitize_path("nul")
        with self.assertRaises(ValueError):
            sanitize_path("con.txt")

        # Safe relative path works
        clean = sanitize_path("uploads/images/sample.jpg")
        self.assertIn("sample.jpg", clean)

    def test_secret_scanning(self):
        # AWS Key detection
        aws_text = "Here is my key: aws_access_key_id = AKIAIOSFODNN7EXAMPLE and something else"
        findings = scan_for_secrets(aws_text)
        self.assertTrue(any(f["type"] == "AWS Access Key ID" for f in findings))

        # OpenAI key detection
        openai_text = "sk-abcdefghijklmnopqrstuvwxyz123456"
        findings = scan_for_secrets(openai_text)
        self.assertTrue(any(f["type"] == "OpenAI/API Key" for f in findings))

        # Clean text
        self.assertEqual(scan_for_secrets("hello world"), [])

    def test_get_cors_origins(self):
        # Custom origins
        custom = ["http://my-domain.com", " https://secure.app "]
        res = get_cors_origins(custom)
        self.assertEqual(res, ["http://my-domain.com", "https://secure.app"])

        # Default fallback
        defaults = get_cors_origins()
        self.assertIn("http://localhost:5173", defaults)
        self.assertIn("http://localhost:3000", defaults)

    def test_audit_action_redacts_secrets(self):
        secret_payload = {
            "camera_name": "Front Door",
            "api_key": "sk-1234567890abcdef1234567890",
            "normal_note": "inspection setup",
        }
        entry = audit_action(
            user_id=42,
            action="camera_create",
            details=secret_payload,
            ip_address="192.168.1.50",
            success=True,
        )
        self.assertEqual(entry["user_id"], 42)
        self.assertEqual(entry["action"], "camera_create")
        self.assertEqual(entry["ip_address"], "192.168.1.50")
        self.assertTrue(entry["success"])
        # Secret in details should be masked
        self.assertNotIn("sk-1234567890abcdef1234567890", str(entry["details"]))
        self.assertIn("<redacted:", entry["details"]["api_key"])
        self.assertEqual(entry["details"]["normal_note"], "inspection setup")


if __name__ == "__main__":
    unittest.main()
