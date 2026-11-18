"""WebSocket endpoints for real-time inspection streams."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session

from app.core.jwt import decode_token
from app.db.session import get_db
from app.db.models import Camera, User
from app.services.camera import CameraStreamer
from app.services.event_bus import event_bus
from app.services.live_feed import inspection_topic
from app.services.metrics import ws_closed, ws_opened
from app.services.tenant import scoped_one

router = APIRouter(prefix="/ws", tags=["websocket"])


AUTH_SUBPROTOCOL_PREFIX = "bearer."


def extract_ws_token(websocket: WebSocket, query_token: str | None) -> tuple[str | None, str | None]:
    """Return (token, negotiated_subprotocol).

    Preferred transport is the ``Sec-WebSocket-Protocol`` header carrying
    ``bearer.<jwt>``, which keeps credentials out of URLs and access logs.
    The ``?token=`` query form is still accepted for backwards compatibility
    but is discouraged and not echoed back.
    """
    for proto in websocket.scope.get("subprotocols", []):
        if proto.startswith(AUTH_SUBPROTOCOL_PREFIX):
            return proto[len(AUTH_SUBPROTOCOL_PREFIX):], proto
    return query_token, None


async def authenticate_ws(token: str | None, db: Session) -> User | None:
    """Authenticate WebSocket connection via bearer token."""
    if not token:
        return None
    try:
        payload = decode_token(token)
        user_id = int(payload.get("user_id", 0))
        if user_id <= 0 or payload.get("sub") != str(user_id):
            return None
        user = db.get(User, user_id)
        if user and user.is_active:
            return user
    except Exception:
        pass
    return None


@router.websocket("/cameras/{camera_id}/stream")
async def camera_stream(
    websocket: WebSocket,
    camera_id: int,
    token: Optional[str] = Query(None),
    inference: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Real-time camera stream with optional inference.

    Authentication via ``Sec-WebSocket-Protocol: bearer.<jwt>`` (preferred).
    Sends JSON messages: {camera_id, session_id, frame_id, timestamp, detections?, status}
    """
    jwt_token, subprotocol = extract_ws_token(websocket, token)
    user = await authenticate_ws(jwt_token, db)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    camera = scoped_one(db, Camera, user, camera_id)
    if not camera:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Camera not found")
        return

    if not camera.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Camera not active")
        return

    streamer = CameraStreamer(camera, db)
    ws_opened("camera_stream")

    try:
        await streamer.stream_frames(websocket, inference=inference, session_id=None, subprotocol=subprotocol)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        streamer.close()
        ws_closed("camera_stream")


@router.websocket("/inspections/live")
async def inspection_events(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Subscribe to inspection result events across all cameras.

    Authentication via ``Sec-WebSocket-Protocol: bearer.<jwt>`` (preferred).
    Messages: {"type": "connected"} then {"type": "inspection.created", inspection: {...}}
    plus periodic {"type": "heartbeat", timestamp}.
    """
    jwt_token, subprotocol = extract_ws_token(websocket, token)
    user = await authenticate_ws(jwt_token, db)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    await websocket.accept(subprotocol=subprotocol)
    await websocket.send_json({"type": "connected", "timestamp": time.time()})
    queue = event_bus.subscribe(inspection_topic(user.org_id))
    ws_opened("inspections_live")

    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                await websocket.send_json(payload)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "timestamp": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        event_bus.unsubscribe(inspection_topic(user.org_id), queue)
        ws_closed("inspections_live")
