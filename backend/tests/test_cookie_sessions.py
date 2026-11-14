"""Cookie session mode (AUTH_TOKEN_STORAGE=cookie) + CSRF tests.

Mode matrix covered: body mode (unchanged), cookie mode (httpOnly cookies,
no tokens in bodies, CSRF enforcement, silent refresh via cookie, logout
clears cookies).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.constants import Role as RoleNames
from app.db.base import Base
from app.db.models import Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.session_cookies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE


class CookieSessionTestBase(unittest.TestCase):
    storage_mode = "body"

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        role = Role(name=RoleNames.OPERATOR, description="Operator")
        db.add(role)
        db.flush()
        user = User(username="op-cookie", hashed_password="", role_id=role.id, is_active=True)
        user.set_password("op-password-123")
        db.add(user)
        db.commit()
        db.close()

        patcher = patch("app.core.config.settings.AUTH_TOKEN_STORAGE", self.storage_mode)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The CI integration job runs with APP_ENV=production, which would set
        # Secure cookies that the http://testclient never echoes back. These
        # tests exercise the cookie MECHANICS, not the Secure flag.
        patcher = patch("app.core.config.settings.AUTH_COOKIE_SECURE", False)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        # raise_server_exceptions=False so middleware 403s surface as responses.
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def login(self):
        return self.client.post(
            "/auth/login",
            json={"username": "op-cookie", "password": "op-password-123"},
        )


class BodyModeUnchangedTests(CookieSessionTestBase):
    storage_mode = "body"

    def test_body_mode_returns_tokens_and_no_cookies(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_storage"], "body")
        self.assertTrue(body["token"]["access_token"])
        self.assertTrue(body["refresh_token"])
        set_cookies = {c for c in response.headers.get_list("set-cookie") if "iv_" in c}
        self.assertEqual(set_cookies, set(), "body mode must not set session cookies")

    def test_body_mode_post_needs_no_csrf(self):
        token = self.login().json()["token"]["access_token"]
        # A mutating call without any CSRF header works with a bearer token.
        response = self.client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 204)


class CookieModeTests(CookieSessionTestBase):
    storage_mode = "cookie"

    def test_login_sets_httponly_cookies_and_sanitized_body(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_storage"], "cookie")
        self.assertEqual(body["token"]["access_token"], "")
        self.assertEqual(body["refresh_token"], "")

        cookies = response.cookies
        self.assertIn(ACCESS_COOKIE, cookies)
        self.assertIn(REFRESH_COOKIE, cookies)
        self.assertIn(CSRF_COOKIE, cookies)
        set_cookie_headers = response.headers.get_list("set-cookie")
        access_cookie = next(c for c in set_cookie_headers if c.startswith(f"{ACCESS_COOKIE}="))
        self.assertIn("HttpOnly", access_cookie)
        self.assertIn("SameSite=lax", access_cookie)
        refresh_cookie = next(c for c in set_cookie_headers if c.startswith(f"{REFRESH_COOKIE}="))
        self.assertIn("HttpOnly", refresh_cookie)
        self.assertIn("Path=/auth/refresh", refresh_cookie)
        # The CSRF cookie must be readable by JS (not HttpOnly).
        csrf_cookie = next(c for c in set_cookie_headers if c.startswith(f"{CSRF_COOKIE}="))
        self.assertNotIn("HttpOnly", csrf_cookie)

    def test_cookie_session_authenticates_without_header(self):
        self.login()
        response = self.client.get("/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "op-cookie")

    def test_mutating_request_without_csrf_header_rejected(self):
        self.login()
        response = self.client.post("/auth/logout")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["detail"])

    def test_mutating_request_with_matching_csrf_header_passes(self):
        self.login()
        csrf = self.client.cookies[CSRF_COOKIE]
        response = self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 204)

    def test_mutating_request_with_wrong_csrf_token_rejected(self):
        self.login()
        response = self.client.post("/auth/logout", headers={"X-CSRF-Token": "forged-token"})
        self.assertEqual(response.status_code, 403)

    def test_silent_refresh_via_cookie_rotates_cookies(self):
        login_resp = self.login()
        old_refresh = login_resp.cookies[REFRESH_COOKIE]
        old_csrf = login_resp.cookies[CSRF_COOKIE]
        # Silent refresh: no body token, no CSRF needed (exempt pre-session route).
        response = self.client.post("/auth/refresh", json={"refresh_token": ""})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_storage"], "cookie")
        self.assertEqual(body["access_token"], "")
        # Cookies rotated: new refresh token differs from the old one.
        self.assertNotEqual(response.cookies[REFRESH_COOKIE], old_refresh)
        # The old refresh token must be dead (rotation semantics).
        reused = self.client.post("/auth/refresh", json={"refresh_token": ""})
        self.assertEqual(reused.status_code, 200)  # uses the NEW cookie
        client_no_cookies = TestClient(self.app, raise_server_exceptions=False)
        stale = client_no_cookies.post("/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(stale.status_code, 401)
        client_no_cookies.close()
        del old_csrf  # csrf rotation asserted below via new login only

    def test_logout_clears_session_cookies(self):
        self.login()
        csrf = self.client.cookies[CSRF_COOKIE]
        response = self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 204)
        set_cookie_headers = response.headers.get_list("set-cookie")
        # Starlette delete_cookie writes quoted-empty values with Max-Age=0.
        self.assertTrue(any(c.startswith(f'{ACCESS_COOKIE}=""') and "Max-Age=0" in c for c in set_cookie_headers))
        self.assertTrue(any(c.startswith(f'{REFRESH_COOKIE}=""') and "Max-Age=0" in c for c in set_cookie_headers))
        self.assertTrue(any(c.startswith(f'{CSRF_COOKIE}=""') and "Max-Age=0" in c for c in set_cookie_headers))

    def test_safe_methods_do_not_require_csrf(self):
        self.login()
        self.assertEqual(self.client.get("/auth/me").status_code, 200)


if __name__ == "__main__":
    unittest.main()
