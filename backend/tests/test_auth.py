"""Authentication, password, bootstrap, and route-level RBAC tests."""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import provision_initial_admin
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db.models import Role, User
from app.db.session import get_db
from app.main import create_app


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        self.admin_role = Role(name=RoleNames.ADMIN, description="Admin")
        self.operator_role = Role(name=RoleNames.OPERATOR, description="Operator")
        db.add_all([self.admin_role, self.operator_role])
        db.flush()
        self.admin = self._user(db, "admin-test", "admin-password-123", self.admin_role, True)
        self.operator = self._user(db, "operator-test", "operator-password-123", self.operator_role, True)
        self.inactive = self._user(db, "inactive-test", "inactive-password-123", self.operator_role, False)
        db.commit()
        self.admin_id = self.admin.id
        self.operator_id = self.operator.id
        self.inactive_id = self.inactive.id
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _user(db, username, password, role, is_active):
        user = User(username=username, hashed_password="", role_id=role.id, is_active=is_active)
        user.set_password(password)
        db.add(user)
        db.flush()
        return user

    def override_db(self):
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def test_password_hashing_and_malformed_hash_handling(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        self.assertFalse(verify_password("anything", "salt$999999999999$hash"))
        self.assertFalse(verify_password("anything", "not-a-password-hash"))

    def test_login_and_current_user(self):
        response = self.client.post(
            "/auth/login",
            json={"username": "operator-test", "password": "operator-password-123"},
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]["access_token"]
        me = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["role"], RoleNames.OPERATOR)

    def test_bad_password_inactive_user_and_missing_token_are_rejected(self):
        bad_password = self.client.post(
            "/auth/login", json={"username": "operator-test", "password": "incorrect"}
        )
        self.assertEqual(bad_password.status_code, 401)
        inactive_login = self.client.post(
            "/auth/login",
            json={"username": "inactive-test", "password": "inactive-password-123"},
        )
        self.assertEqual(inactive_login.status_code, 403)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        inactive_token = create_access_token(self.inactive_id, RoleNames.OPERATOR)
        self.assertEqual(
            self.client.get(
                "/auth/me", headers={"Authorization": f"Bearer {inactive_token}"}
            ).status_code,
            401,
        )

    def test_expired_and_malformed_tokens_are_rejected(self):
        expired = create_access_token(self.operator_id, RoleNames.OPERATOR, expires_minutes=-1)
        expired_response = self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {expired}"}
        )
        malformed_response = self.client.get(
            "/auth/me", headers={"Authorization": "Bearer not.a.valid.token"}
        )
        self.assertEqual(expired_response.status_code, 401)
        self.assertEqual(malformed_response.status_code, 401)

    def test_operator_can_read_but_cannot_manage_cameras_or_users(self):
        token = create_access_token(self.operator_id, RoleNames.OPERATOR)
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/cameras", headers=headers).status_code, 200)
        camera_response = self.client.post(
            "/cameras",
            headers=headers,
            json={"name": "Forbidden camera", "source_type": "simulated"},
        )
        user_response = self.client.post(
            "/admin/users",
            headers=headers,
            json={"username": "attacker", "password": "long-enough-password", "role": "admin"},
        )
        self.assertEqual(camera_response.status_code, 403)
        self.assertEqual(user_response.status_code, 403)

    def test_admin_can_create_users_and_camera_configuration(self):
        token = create_access_token(self.admin_id, RoleNames.ADMIN)
        headers = {"Authorization": f"Bearer {token}"}
        create_user = self.client.post(
            "/admin/users",
            headers=headers,
            json={"username": "new-operator", "password": "new-operator-password", "role": "operator"},
        )
        create_camera = self.client.post(
            "/cameras",
            headers=headers,
            json={"name": "Admin camera", "source_type": "simulated"},
        )
        self.assertEqual(create_user.status_code, 201, create_user.text)
        self.assertEqual(create_camera.status_code, 201, create_camera.text)

    def test_rtsp_credentials_are_rejected_and_redacted_in_output_schema(self):
        response = self.client.post(
            "/cameras",
            headers={"Authorization": f"Bearer {create_access_token(self.admin_id, RoleNames.ADMIN)}"},
            json={"name": "Credential camera", "source_type": "rtsp", "source_url": "rtsp://operator:secret@camera.local/live"},
        )
        self.assertEqual(response.status_code, 422)
        from app.schemas.camera import CameraOut

        payload = CameraOut(
            id=1,
            name="legacy",
            source_type="rtsp",
            status="connected",
            source_url="rtsp://operator:secret@camera.local/live",
            is_active=True,
        )
        self.assertNotIn("secret", payload.model_dump_json())
        self.assertIn("[REDACTED]@camera.local", payload.model_dump_json())

    def test_bootstrap_creates_first_admin_without_default_credentials(self):
        bootstrap_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bootstrap_engine)
        factory = sessionmaker(bind=bootstrap_engine, expire_on_commit=False)
        db = factory()
        try:
            admin = provision_initial_admin(db, "first-admin", "a-unique-admin-password")
            self.assertEqual(admin.role.name, RoleNames.ADMIN)
            self.assertTrue(admin.check_password("a-unique-admin-password"))
            with self.assertRaises(ValueError):
                provision_initial_admin(db, "second-admin", "another-unique-admin-password")
        finally:
            db.close()
            bootstrap_engine.dispose()

    def test_bootstrap_rejects_short_password(self):
        bootstrap_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bootstrap_engine)
        db = sessionmaker(bind=bootstrap_engine)()
        try:
            with self.assertRaises(ValueError):
                provision_initial_admin(db, "first-admin", "short")
        finally:
            db.close()
            bootstrap_engine.dispose()


if __name__ == "__main__":
    unittest.main()
