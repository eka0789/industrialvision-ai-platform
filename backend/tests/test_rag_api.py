"""RAG API tests: RBAC matrix, org isolation, upload→ingest e2e, chat refusal."""

from __future__ import annotations

import time
import unittest
import unittest.mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings as app_settings
from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import AuditLog, Organization, Role, User
from app.db.session import get_db
from app.main import create_app
from app.services.task_queue import task_queue


class RagApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for attr, value in (("EVIDENCE_ROOT", self._tmp.name), ("RAG_LLM_PROVIDER", "mock-demo")):
            patcher = unittest.mock.patch.object(app_settings, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = self.session_factory()
        db.add(Organization(id=1, name="A", slug="a"))
        db.add(Organization(id=2, name="B", slug="b"))
        roles = {name: Role(name=name, description=name) for name in RoleNames.all()}
        db.add_all(roles.values())
        db.flush()

        def make(username: str, role: str, org: int) -> User:
            user = User(username=username, hashed_password="", role_id=roles[role].id, is_active=True, org_id=org)
            user.role_name = role
            db.add(user)
            return user

        self.users = {
            "owner_a": make("owner_a", RoleNames.OWNER, 1),
            "admin_a": make("admin_a", RoleNames.ADMIN, 1),
            "operator_a": make("operator_a", RoleNames.OPERATOR, 1),
            "viewer_a": make("viewer_a", RoleNames.VIEWER, 1),
            "viewer_b": make("viewer_b", RoleNames.VIEWER, 2),
        }
        db.commit()
        db.close()

        # Background tasks use the global SessionLocal; point it at the test DB
        # so in-process queue execution writes to the same isolated database.
        import app.db.session as db_session

        patcher = unittest.mock.patch.object(db_session, "SessionLocal", self.session_factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Chat rate-limit state is process-global; isolate between tests.
        import app.api.v1.routers.rag as rag_router

        self.addCleanup(rag_router._chat_hits.clear)

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

    def auth(self, username: str) -> dict[str, str]:
        user = self.users[username]
        return {"Authorization": f"Bearer {create_access_token(user.id, user.role_name)}"}

    def upload(self, username: str, filename: str, content: bytes, mime: str = "text/plain"):
        return self.client.post(
            "/rag/documents",
            headers=self.auth(username),
            files={"file": (filename, content, mime)},
        )

    def wait_document_ready(self, document_id: int, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.client.get(f"/rag/documents/{document_id}", headers=self.auth("admin_a")).json()
            if body["status"] in ("ready", "failed"):
                return body
            time.sleep(0.1)
        raise AssertionError("document ingestion did not finish in time")

    def test_rbac_matrix(self):
        cases = [
            ("viewer_a", "get", "/rag/documents", 200),
            ("viewer_a", "post", "/rag/documents", 403),
            ("operator_a", "post", "/rag/documents", 202),
            ("operator_a", "delete", "/rag/documents/999", 403),
            ("admin_a", "delete", "/rag/documents/999", 404),  # admin can delete, unknown id → 404
            ("viewer_a", "post", "/rag/chat", 200),
        ]
        for username, method, path, expected in cases:
            headers = self.auth(username)
            if method == "post" and path == "/rag/documents":
                response = self.upload(username, "doc.txt", b"hello world maintenance")
            elif method == "post" and path == "/rag/chat":
                response = self.client.post(path, headers=headers, json={"question": "anything"})
            else:
                response = getattr(self.client, method)(path, headers=headers)
            self.assertEqual(response.status_code, expected, f"{username} {method} {path} → {response.status_code}")

    def test_upload_ingests_and_chats(self):
        content = (
            "The safety helmet standard requires inspection before every shift. "
            "Cracked helmets must be removed from service immediately. "
            "Replace helmet suspension after any significant impact."
        ).encode("utf-8")
        response = self.upload("operator_a", "helmet-policy.txt", content)
        self.assertEqual(response.status_code, 202)
        document_id = response.json()["id"]

        body = self.wait_document_ready(document_id)
        self.assertEqual(body["status"], "ready")
        self.assertGreater(body["chunk_count"], 0)
        self.assertEqual(body["embedding_provider"], "local-hashing")

        chat = self.client.post(
            "/rag/chat", headers=self.auth("viewer_a"), json={"question": "when must helmets be inspected?"}
        )
        self.assertEqual(chat.status_code, 200)
        payload = chat.json()
        self.assertEqual(payload["status"], "answered")
        self.assertEqual(payload["provider"], "mock-demo")
        self.assertTrue(payload["citations"])
        # Every citation links back to a real chunk id of this document.
        chunks = self.client.get(f"/rag/documents/{document_id}/chunks", headers=self.auth("admin_a")).json()
        chunk_ids = {row["id"] for row in chunks}
        self.assertTrue({citation["chunk_id"] for citation in payload["citations"]} <= chunk_ids)

        # Audit trail for upload.
        db = self.session_factory()
        try:
            self.assertTrue(
                db.query(AuditLog).filter(AuditLog.action == "rag.document.uploaded").first()
            )
        finally:
            db.close()

    def test_upload_rejects_disallowed_types(self):
        response = self.upload("operator_a", "malware.exe", b"MZ\x90\x00", "application/x-msdownload")
        self.assertEqual(response.status_code, 415)
        response = self.upload("operator_a", "fake.pdf", b"not really", "application/pdf")
        self.assertEqual(response.status_code, 415)

    def test_cross_tenant_documents_isolated(self):
        response = self.upload("operator_a", "secret-a.txt", b"org alpha proprietary data torque specs", mime="text/plain")
        document_id = response.json()["id"]
        self.wait_document_ready(document_id)

        self.assertEqual(
            self.client.get(f"/rag/documents/{document_id}", headers=self.auth("viewer_b")).status_code, 404
        )
        chat = self.client.post(
            "/rag/chat", headers=self.auth("viewer_b"), json={"question": "org alpha torque specs?"}
        )
        self.assertEqual(chat.json()["status"], "refused", "org B must not retrieve org A content")

    def test_chat_rate_limit(self):
        from app.api.v1.routers.rag import _CHAT_MAX_PER_WINDOW

        for index in range(_CHAT_MAX_PER_WINDOW):
            response = self.client.post(
                "/rag/chat", headers=self.auth("viewer_a"), json={"question": f"question number {index}"}
            )
            self.assertEqual(response.status_code, 200)
        limited = self.client.post("/rag/chat", headers=self.auth("viewer_a"), json={"question": "one more"})
        self.assertEqual(limited.status_code, 429)

    def test_delete_is_audited_and_cascades(self):
        response = self.upload("operator_a", "temp.txt", b"delete me content")
        document_id = response.json()["id"]
        self.wait_document_ready(document_id)
        deleted = self.client.delete(f"/rag/documents/{document_id}", headers=self.auth("admin_a"))
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(
            self.client.get(f"/rag/documents/{document_id}", headers=self.auth("admin_a")).status_code, 404
        )
        db = self.session_factory()
        try:
            self.assertTrue(db.query(AuditLog).filter(AuditLog.action == "rag.document.deleted").first())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
