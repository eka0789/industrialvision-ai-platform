"""Tests for shift-scoped refresh tokens: issuance, rotation, and revocation."""

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
from app.db.models import RefreshToken, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.refresh_tokens import issue_refresh_token, validate_refresh_token


class RefreshTokenTests(unittest.TestCase):
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
        user = User(username="refresh-op", hashed_password="", role_id=role.id, is_active=True)
        user.set_password("pass-123456789")
        db.add(user)
        db.commit()
        self.user_id = user.id
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

    def test_login_returns_refresh_token(self):
        resp = self.client.post(
            "/auth/login",
            json={"username": "refresh-op", "password": "pass-123456789"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("refresh_token", body)
        self.assertTrue(len(body["refresh_token"]) >= 32)

    def test_refresh_token_rotation_issues_new_token_and_revokes_old(self):
        login = self.client.post(
            "/auth/login",
            json={"username": "refresh-op", "password": "pass-123456789"},
        )
        first_refresh = login.json()["refresh_token"]

        # First refresh succeeds
        refreshed = self.client.post("/auth/refresh", json={"refresh_token": first_refresh})
        self.assertEqual(refreshed.status_code, 200)
        data = refreshed.json()
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        second_refresh = data["refresh_token"]
        self.assertNotEqual(first_refresh, second_refresh)

        # Trying to re-use the first refresh token is rejected (rotation protection)
        reused = self.client.post("/auth/refresh", json={"refresh_token": first_refresh})
        self.assertEqual(reused.status_code, 401)

        # But the newly issued second token is valid
        third = self.client.post("/auth/refresh", json={"refresh_token": second_refresh})
        self.assertEqual(third.status_code, 200)

    def test_logout_revokes_all_user_refresh_tokens(self):
        login = self.client.post(
            "/auth/login",
            json={"username": "refresh-op", "password": "pass-123456789"},
        )
        access = login.json()["token"]["access_token"]
        refresh = login.json()["refresh_token"]

        logout = self.client.post("/auth/logout", headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(logout.status_code, 204)

        # Refresh with that token now fails
        after_logout = self.client.post("/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(after_logout.status_code, 401)

    def test_expired_refresh_token_is_rejected(self):
        db = self.sf()
        try:
            user = db.get(User, self.user_id)
            plain, row = issue_refresh_token(db, user, "127.0.0.1", "test")
            # Force expiration
            row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()
        finally:
            db.close()

        resp = self.client.post("/auth/refresh", json={"refresh_token": plain})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
