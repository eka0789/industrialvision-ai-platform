"""PdM API tests: RBAC matrix, org isolation, approval workflow, audit logs."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import AuditLog, Machine, Organization, Role, User
from app.db.session import get_db
from app.main import create_app


class PdmApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        org_a = Organization(id=1, name="A", slug="a")
        org_b = Organization(id=2, name="B", slug="b")
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all([org_a, org_b, *roles.values()])
        db.flush()

        def make(username: str, role: str, org: int) -> User:
            user = User(username=username, hashed_password="", role_id=roles[role].id, is_active=True, org_id=org)
            user.set_password(f"{username}-password-1")
            db.add(user)
            return user

        self.users = {
            "owner_a": self._wrap(make("owner_a", RoleNames.OWNER, 1), RoleNames.OWNER),
            "admin_a": self._wrap(make("admin_a", RoleNames.ADMIN, 1), RoleNames.ADMIN),
            "operator_a": self._wrap(make("operator_a", RoleNames.OPERATOR, 1), RoleNames.OPERATOR),
            "engineer_a": self._wrap(make("engineer_a", RoleNames.AI_ENGINEER, 1), RoleNames.AI_ENGINEER),
            "viewer_a": self._wrap(make("viewer_a", RoleNames.VIEWER, 1), RoleNames.VIEWER),
            "admin_b": self._wrap(make("admin_b", RoleNames.ADMIN, 2), RoleNames.ADMIN),
        }
        db.flush()
        self.machine_a = Machine(name="Press A", code="PR-A1", machine_type="press", status="running", org_id=1)
        self.machine_b = Machine(name="Press B", code="PR-B1", machine_type="press", status="running", org_id=2)
        db.add_all([self.machine_a, self.machine_b])
        db.commit()
        self.ids = {"machine_a": self.machine_a.id, "machine_b": self.machine_b.id}
        db.close()

        self.app = create_app()
        self.app.dependency_overrides[get_db] = self.override_db
        self.client = TestClient(self.app)

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

    @staticmethod
    def _wrap(user: User, role_name: str):
        """Keep id + role after the setUp session detaches the instance."""
        user.role_name = role_name
        return user

    def auth(self, username: str) -> dict[str, str]:
        user = self.users[username]
        return {"Authorization": f"Bearer {create_access_token(user.id, user.role_name)}"}

    def _reading_payload(self, **overrides):
        payload = {
            "air_temperature_k": 298.0,
            "process_temperature_k": 308.0,
            "rotational_speed_rpm": 1500.0,
            "torque_nm": 26.0,
            "tool_wear_min": 100.0,
        }
        payload.update(overrides)
        return payload

    # ── RBAC matrix ──────────────────────────────────────────────────────────

    def test_rbac_matrix(self):
        cases = [
            ("viewer_a", "get", "/pdm/machines", 200),
            ("viewer_a", "post", "/pdm/machines", 403),
            ("operator_a", "post", "/pdm/machines", 201),
            ("engineer_a", "post", "/pdm/machines", 201),
            ("viewer_a", "post", f"/pdm/machines/{self.ids['machine_a']}/readings", 403),
            ("operator_a", "post", f"/pdm/machines/{self.ids['machine_a']}/readings", 201),
            ("viewer_a", "post", f"/pdm/machines/{self.ids['machine_a']}/predict", 200),
            ("operator_a", "post", "/pdm/requests", 201),
            ("viewer_a", "post", "/pdm/requests", 403),
        ]
        for username, method, path, expected in cases:
            headers = self.auth(username)
            if path.endswith("/readings") and method == "post":
                response = self.client.post(path, headers=headers, json=self._reading_payload())
            elif path.endswith("/predict"):
                self.client.post(
                    f"/pdm/machines/{self.ids['machine_a']}/readings",
                    headers=self.auth("operator_a"),
                    json=self._reading_payload(),
                )
                response = self.client.post(path, headers=headers)
            elif path == "/pdm/requests" and method == "post":
                response = self.client.post(
                    path, headers=headers, json={"machine_id": self.ids["machine_a"], "title": "Check bearing"}
                )
            elif path == "/pdm/machines" and method == "post":
                response = self.client.post(path, headers=headers, json={"name": "RBAC Machine", "code": f"RBAC-{username}"})
            else:
                response = self.client.get(path, headers=headers)
            self.assertEqual(
                response.status_code, expected, f"{username} {method} {path}: {response.status_code} != {expected}: {response.text[:200]}"
            )

    # ── Org isolation ────────────────────────────────────────────────────────

    def test_cross_tenant_machine_access_denied(self):
        headers = self.auth("admin_a")
        self.assertEqual(self.client.get(f"/pdm/machines/{self.ids['machine_b']}", headers=headers).status_code, 404)
        self.assertEqual(
            self.client.post(f"/pdm/machines/{self.ids['machine_b']}/readings", headers=headers, json=self._reading_payload()).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/pdm/machines/{self.ids['machine_b']}/predict", headers=headers).status_code, 404
        )
        listing = self.client.get("/pdm/machines", headers=headers).json()
        self.assertEqual([row["code"] for row in listing], ["PR-A1"])

    # ── Prediction contract ──────────────────────────────────────────────────

    def test_prediction_reports_rules_fallback_explicitly(self):
        self.client.post(
            f"/pdm/machines/{self.ids['machine_a']}/readings", headers=self.auth("operator_a"), json=self._reading_payload()
        )
        response = self.client.post(f"/pdm/machines/{self.ids['machine_a']}/predict", headers=self.auth("viewer_a"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "rules-only-fallback")
        self.assertIn("note", body)
        self.assertIsNone(body["failure_probability"])  # no fabricated number
        self.assertIsNotNone(body["health"])

    def test_prediction_without_readings_is_error_not_garbage(self):
        response = self.client.post(f"/pdm/machines/{self.ids['machine_a']}/predict", headers=self.auth("operator_a"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "error")
        self.assertIn("no sensor readings", body["error"])

    # ── Approval workflow ────────────────────────────────────────────────────

    def _create_request(self, creator: str = "operator_a") -> int:
        response = self.client.post(
            "/pdm/requests",
            headers=self.auth(creator),
            json={"machine_id": self.ids["machine_a"], "title": "Replace spindle", "priority": "high"},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_request_approval_gate(self):
        request_id = self._create_request("operator_a")

        # Viewer cannot approve (RBAC); operator cannot approve (role); owner can.
        self.assertEqual(
            self.client.post(f"/pdm/requests/{request_id}/approve", headers=self.auth("viewer_a"), json={}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/pdm/requests/{request_id}/approve", headers=self.auth("operator_a"), json={}).status_code,
            403,
        )
        approved = self.client.post(
            f"/pdm/requests/{request_id}/approve", headers=self.auth("owner_a"), json={"note": "schedule Friday"}
        )
        self.assertEqual(approved.status_code, 200)
        body = approved.json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["decided_by_user_id"], self.users["owner_a"].id)

        # Approval cannot be bypassed/repeated: approving again conflicts.
        self.assertEqual(
            self.client.post(f"/pdm/requests/{request_id}/approve", headers=self.auth("admin_a"), json={}).status_code,
            409,
        )

    def test_self_approval_denied(self):
        request_id = self._create_request("owner_a")  # created by owner
        response = self.client.post(
            f"/pdm/requests/{request_id}/approve", headers=self.auth("owner_a"), json={}
        )
        self.assertEqual(response.status_code, 403)

    def test_completion_requires_approval(self):
        request_id = self._create_request("operator_a")
        response = self.client.post(f"/pdm/requests/{request_id}/complete", headers=self.auth("operator_a"))
        self.assertEqual(response.status_code, 409)
        self.client.post(f"/pdm/requests/{request_id}/approve", headers=self.auth("admin_a"), json={})
        response = self.client.post(f"/pdm/requests/{request_id}/complete", headers=self.auth("operator_a"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")

    def test_cross_tenant_request_denied(self):
        request_id = self._create_request("operator_a")  # org 1
        response = self.client.post(
            f"/pdm/requests/{request_id}/approve", headers=self.auth("admin_b"), json={}
        )
        self.assertEqual(response.status_code, 404)

    # ── Audit coverage ───────────────────────────────────────────────────────

    def test_state_changes_are_audit_logged(self):
        machine_id = self.ids["machine_a"]
        self.client.post("/pdm/machines", headers=self.auth("admin_a"), json={"name": "M2", "code": "M-2"})
        self.client.post(f"/pdm/machines/{machine_id}/readings", headers=self.auth("operator_a"), json=self._reading_payload())
        request_id = self._create_request("operator_a")
        self.client.post(f"/pdm/requests/{request_id}/approve", headers=self.auth("owner_a"), json={})

        db = self.session_factory()
        try:
            actions = {row.action for row in db.query(AuditLog).all()}
            self.assertIn("pdm.machine.created", actions)
            self.assertIn("pdm.reading.recorded", actions)
            self.assertIn("pdm.request.created", actions)
            self.assertIn("pdm.request.approved", actions)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
