"""Tests for account lockout, login attempts recording, and audit logging."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.db.base import Base
from app.db.models import AuditLog, LoginAttempt, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.auth_security import is_account_locked


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.sf()
        role = Role(name=RoleNames.OPERATOR, description="Operator")
        db.add(role)
        db.flush()
        user = User(username="lock-target", hashed_password="", role_id=role.id, is_active=True)
        user.set_password("correct-password-123")
        db.add(user)
        db.commit()
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self._override_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _override_db(self):
        db = self.sf()
        try:
            yield db
        finally:
            db.close()

    def test_failed_attempts_produce_audit_logs_and_eventual_lockout(self):
        for index in range(settings.LOGIN_MAX_FAILURES):
            resp = self.client.post(
                "/auth/login",
                json={"username": "lock-target", "password": "wrong-password"},
            )
            self.assertEqual(resp.status_code, 401, f"Attempt {index + 1} expected 401")

        db = self.sf()
        try:
            self.assertTrue(is_account_locked(db, "lock-target"))
            failures = db.query(LoginAttempt).filter(LoginAttempt.username == "lock-target").count()
            self.assertEqual(failures, settings.LOGIN_MAX_FAILURES)
            audit_failures = db.query(AuditLog).filter(AuditLog.action == "auth.login_failure").count()
            self.assertEqual(audit_failures, settings.LOGIN_MAX_FAILURES)
        finally:
            db.close()

        # Subsequent attempts within the window are rejected with 429 even with correct password
        locked_resp = self.client.post(
            "/auth/login",
            json={"username": "lock-target", "password": "correct-password-123"},
        )
        self.assertEqual(locked_resp.status_code, 429)
        self.assertIn("Retry-After", locked_resp.headers)

    def test_successful_login_resets_failure_counter_and_logs_success(self):
        # Two bad attempts followed by a good one
        for _ in range(2):
            self.client.post(
                "/auth/login",
                json={"username": "lock-target", "password": "wrong-password"},
            )
        good = self.client.post(
            "/auth/login",
            json={"username": "lock-target", "password": "correct-password-123"},
        )
        self.assertEqual(good.status_code, 200)

        db = self.sf()
        try:
            self.assertFalse(is_account_locked(db, "lock-target"))
            # Failures cleared on success
            remaining_failures = (
                db.query(LoginAttempt)
                .filter(LoginAttempt.username == "lock-target", LoginAttempt.success.is_(False))
                .count()
            )
            self.assertEqual(remaining_failures, 0)
            audit_success = db.query(AuditLog).filter(AuditLog.action == "auth.login_success").count()
            self.assertEqual(audit_success, 1)
        finally:
            db.close()

    def test_logout_records_audit_log(self):
        login = self.client.post(
            "/auth/login",
            json={"username": "lock-target", "password": "correct-password-123"},
        )
        token = login.json()["token"]["access_token"]
        logout = self.client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout.status_code, 204)

        db = self.sf()
        try:
            audit = db.query(AuditLog).filter(AuditLog.action == "auth.logout").first()
            self.assertIsNotNone(audit)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
