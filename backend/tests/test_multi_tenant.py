"""Cross-tenant isolation tests (A6) — one suite per existing router.

Two organizations are seeded; every test asserts that org-A users can neither
see nor modify org-B resources, and that org-scoped listings only return
their own rows. Isolation is by query construction (foreign ids 404), so no
existence information leaks across tenants.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import (
    AuditLog,
    Camera,
    Inspection,
    InspectionSession,
    ModelVersion,
    Organization,
    Role,
    User,
)
from app.db.session import get_db
from app.main import create_app


class MultiTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()

        org_a = Organization(id=1, name="Org A", slug="org-a")
        org_b = Organization(id=2, name="Org B", slug="org-b")
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all([org_a, org_b, *roles.values()])
        db.flush()

        self.user_a = User(username="alice", hashed_password="", role_id=roles[RoleNames.ADMIN].id,
                           is_active=True, org_id=org_a.id)
        self.user_a.set_password("alice-password-1")
        self.user_b = User(username="bob", hashed_password="", role_id=roles[RoleNames.ADMIN].id,
                           is_active=True, org_id=org_b.id)
        self.user_b.set_password("bob-password-12")
        db.add_all([self.user_a, self.user_b])
        db.flush()

        # Resources in both orgs.
        self.camera_a = Camera(name="CamA", source_type="simulated", is_active=True, org_id=org_a.id)
        self.camera_b = Camera(name="CamB", source_type="simulated", is_active=True, org_id=org_b.id)
        self.model_a = ModelVersion(name="model-a", framework="onnx", is_active=True, org_id=org_a.id)
        self.model_b = ModelVersion(name="model-b", framework="onnx", is_active=True, org_id=org_b.id)
        db.add_all([self.camera_a, self.camera_b, self.model_a, self.model_b])
        db.flush()

        self.session_a = InspectionSession(camera_id=self.camera_a.id, model_version_id=self.model_a.id, org_id=org_a.id)
        self.session_b = InspectionSession(camera_id=self.camera_b.id, model_version_id=self.model_b.id, org_id=org_b.id)
        db.add_all([self.session_a, self.session_b])
        db.flush()
        self.inspection_a = Inspection(session_id=self.session_a.id, camera_id=self.camera_a.id,
                                       decision="PASS", org_id=org_a.id)
        self.inspection_b = Inspection(session_id=self.session_b.id, camera_id=self.camera_b.id,
                                       decision="FAIL", org_id=org_b.id)
        db.add_all([self.inspection_a, self.inspection_b])
        db.commit()

        self.ids = {
            "camera_a": self.camera_a.id, "camera_b": self.camera_b.id,
            "model_a": self.model_a.id, "model_b": self.model_b.id,
            "inspection_a": self.inspection_a.id, "inspection_b": self.inspection_b.id,
            "session_a": self.session_a.id, "session_b": self.session_b.id,
            "user_a": self.user_a.id, "user_b": self.user_b.id,
        }
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

    def auth(self, user: User) -> dict[str, str]:
        # Role is cached during setUp; both fixture users are org admins.
        return {"Authorization": f"Bearer {create_access_token(user.id, RoleNames.ADMIN)}"}

    def alice(self) -> dict[str, str]:
        return self.auth(self.user_a)

    def bob(self) -> dict[str, str]:
        return self.auth(self.user_b)

    # -- cameras ------------------------------------------------------------

    def test_camera_list_is_org_scoped(self):
        response = self.client.get("/cameras", headers=self.alice())
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()]
        self.assertEqual(names, ["CamA"])

    def test_camera_read_update_delete_across_tenant_denied(self):
        headers = self.alice()
        self.assertEqual(self.client.get(f"/cameras/{self.ids['camera_b']}", headers=headers).status_code, 404)
        self.assertEqual(
            self.client.put(f"/cameras/{self.ids['camera_b']}", headers=headers, json={"name": "Hijacked"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/cameras/{self.ids['camera_b']}", headers=headers).status_code, 404)
        self.assertEqual(self.client.post(f"/cameras/{self.ids['camera_b']}/start", headers=headers).status_code, 404)
        self.assertEqual(self.client.post(f"/cameras/{self.ids['camera_b']}/stop", headers=headers).status_code, 404)

    def test_camera_session_creation_across_tenant_denied(self):
        response = self.client.post(
            f"/cameras/{self.ids['camera_b']}/sessions",
            headers=self.alice(),
            json={"camera_id": self.ids["camera_b"], "model_version_id": self.ids["model_a"]},
        )
        self.assertEqual(response.status_code, 404)

    def test_camera_create_stamps_caller_org(self):
        response = self.client.post(
            "/cameras",
            headers=self.alice(),
            json={"name": "NewCam", "source_type": "simulated"},
        )
        self.assertEqual(response.status_code, 201)
        db = self.session_factory()
        try:
            row = db.query(Camera).filter(Camera.name == "NewCam").first()
            self.assertEqual(row.org_id, 1)
        finally:
            db.close()

    # -- models ---------------------------------------------------------------

    def test_model_read_update_activate_across_tenant_denied(self):
        headers = self.alice()
        self.assertEqual(self.client.get(f"/models/{self.ids['model_b']}", headers=headers).status_code, 404)
        self.assertEqual(
            self.client.patch(f"/models/{self.ids['model_b']}", headers=headers, json={"description": "x"}).status_code,
            404,
        )
        self.assertEqual(self.client.post(f"/models/{self.ids['model_b']}/activate", headers=headers).status_code, 404)
        self.assertEqual(self.client.get(f"/models/{self.ids['model_b']}/evaluations", headers=headers).status_code, 404)

    def test_model_list_is_org_scoped_and_names_can_collide_across_orgs(self):
        response = self.client.get("/models/", headers=self.alice())
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()["items"]]
        self.assertEqual(names, ["model-a"])

        # Same model name in a different org does not collide: the request
        # passes the tenant-scoped name check and fails later on artifact
        # resolution (422), proving the collision check is org-scoped.
        response = self.client.post(
            "/models/",
            headers=self.bob(),
            json={"name": "model-a", "framework": "onnx", "task_type": "object_detection"},
        )
        self.assertEqual(response.status_code, 422)
        # (name collision inside org A still applies)
        response = self.client.post(
            "/models/",
            headers=self.alice(),
            json={"name": "model-a", "framework": "onnx", "task_type": "object_detection"},
        )
        self.assertIn(response.status_code, (409, 422))

    # -- inspections ----------------------------------------------------------

    def test_inspection_list_is_org_scoped(self):
        response = self.client.get("/inspections", headers=self.alice())
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["items"]]
        self.assertEqual(ids, [self.ids["inspection_a"]])

    def test_inspection_detail_and_evidence_across_tenant_denied(self):
        headers = self.alice()
        self.assertEqual(
            self.client.get(f"/inspections/{self.ids['inspection_b']}", headers=headers).status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/inspections/{self.ids['inspection_b']}/evidence", headers=headers).status_code, 404
        )

    # -- admin / users ----------------------------------------------------------

    def test_admin_user_list_is_org_scoped(self):
        response = self.client.get("/admin/users", headers=self.alice())
        self.assertEqual(response.status_code, 200)
        usernames = [row["username"] for row in response.json()]
        self.assertEqual(usernames, ["alice"])

    def test_admin_cannot_modify_foreign_org_user(self):
        response = self.client.patch(
            f"/admin/users/{self.ids['user_b']}/status",
            params={"is_active": False},
            headers=self.alice(),
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_user_create_is_audited_and_org_stamped(self):
        response = self.client.post(
            "/admin/users",
            headers=self.alice(),
            json={"username": "carol", "password": "carol-password-123", "role": "operator"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["org_id"], 1)
        db = self.session_factory()
        try:
            log = (
                db.query(AuditLog)
                .filter(AuditLog.action == "admin.user.created")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.org_id, 1)
            self.assertEqual(log.details_json["username"], "carol")
        finally:
            db.close()

    def test_admin_cannot_mint_owner(self):
        response = self.client.post(
            "/admin/users",
            headers=self.alice(),  # admin (not owner)
            json={"username": "sneaky", "password": "sneaky-password-1", "role": "owner"},
        )
        self.assertEqual(response.status_code, 403)

    # -- WS live feed topic isolation (bus-level, no HTTP) -----------------------

    def test_live_events_are_org_scoped_topics(self):
        from app.services.event_bus import EventBus
        from app.services.live_feed import inspection_topic

        bus = EventBus()
        queue_a = bus.subscribe(inspection_topic(1))
        queue_b = bus.subscribe(inspection_topic(2))
        bus.publish(inspection_topic(1), {"type": "inspection.created", "org": 1})
        self.assertEqual(queue_a.get_nowait()["org"], 1)
        self.assertTrue(queue_b.empty())


if __name__ == "__main__":
    unittest.main()
