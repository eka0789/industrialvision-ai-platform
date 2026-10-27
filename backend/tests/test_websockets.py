"""Tests for WebSocket authentication and event routing."""

from __future__ import annotations

import unittest
from starlette.websockets import WebSocketDisconnect

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.constants import Role as RoleNames
from app.core.jwt import create_access_token
from app.db.base import Base
from app.db.models import Camera, Role, User
from app.db.session import get_db
from app.main import create_app


class WebSocketAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        sf = sessionmaker(bind=self.engine, expire_on_commit=False)
        db = sf()
        role = Role(name=RoleNames.OPERATOR, description="Operator")
        db.add(role)
        db.flush()
        user = User(username="op_ws", hashed_password="", role_id=role.id, is_active=True)
        user.set_password("pw")
        camera = Camera(name="WsCam", source_type="simulated", is_active=True)
        db.add_all([user, camera])
        db.commit()
        self.user_id = user.id
        self.camera_id = camera.id
        self.token = create_access_token(user.id, RoleNames.OPERATOR)
        db.close()
        self.sf = sf
        self.app = create_app()
        self.app.dependency_overrides[get_db] = self._override_db
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def _override_db(self):
        db = self.sf()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()

    def test_ws_camera_stream_rejects_missing_token(self):
        """No token → server closes WS immediately (policy violation)."""
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect(
                f"/ws/cameras/{self.camera_id}/stream"
            ):
                pass
        self.assertIn(ctx.exception.code, (1008, 1000))

    def test_ws_inspections_live_rejects_missing_token(self):
        """No token → server closes WS immediately."""
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/ws/inspections/live"):
                pass
        self.assertIn(ctx.exception.code, (1008, 1000))

    def test_ws_camera_stream_rejects_invalid_token(self):
        """Bad token → server closes WS."""
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                f"/ws/cameras/{self.camera_id}/stream?token=not-a-valid-jwt"
            ):
                pass

    def test_ws_camera_not_found_rejects(self):
        """Valid token but nonexistent camera → server closes WS."""
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                f"/ws/cameras/99999/stream?token={self.token}"
            ):
                pass

    def test_ws_inspections_live_accepts_bearer_subprotocol(self):
        """Token in Sec-WebSocket-Protocol authenticates and is echoed back."""
        with self.client.websocket_connect(
            "/ws/inspections/live",
            subprotocols=[f"bearer.{self.token}"],
        ) as ws:
            self.assertEqual(ws.accepted_subprotocol, f"bearer.{self.token}")
            self.assertEqual(ws.receive_json()["type"], "connected")

    def test_ws_inspections_live_rejects_token_in_query_when_bearer_missing(self):
        """Query token still works for old clients, but no subprotocol is echoed."""
        with self.client.websocket_connect(
            f"/ws/inspections/live?token={self.token}"
        ) as ws:
            self.assertIsNone(ws.accepted_subprotocol)
            self.assertEqual(ws.receive_json()["type"], "connected")

    def test_ws_inspections_live_rejects_invalid_bearer_subprotocol(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/ws/inspections/live",
                subprotocols=["bearer.not-a-valid-jwt"],
            ):
                pass


if __name__ == "__main__":
    unittest.main()
