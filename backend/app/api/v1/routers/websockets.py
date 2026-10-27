"""WebSocket endpoints for real-time inspection streams."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session

from app.core.jwt import decode_token
from app.db.session import SessionLocal
from app.db.models import Camera, User
from app.services.camera import CameraStreamer
from app.services.event_bus import event_bus
from app.services.live_feed import TOPIC_INSPECTIONS

router = APIRouter(prefix="/ws", tags=["websocket"])


async def authenticate_ws(token: str | None, db: Session) -> User | None:
    """Authenticate WebSocket connection via query token."""
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
):
    """Real-time camera stream with optional inference.
    
    Authentication via ?token=<jwt>
    Sends JSON messages: {camera_id, session_id, frame_id, timestamp, detections?, status}
    """
    db = SessionLocal()
    try:
        user = await authenticate_ws(token, db)
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
            return

        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Camera not found")
            return

        if not camera.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Camera not active")
            return

        streamer = CameraStreamer(camera, db)
        
        try:
            await streamer.stream_frames(websocket, inference=inference, session_id=None)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            try:
                await websocket.send_json({"error": str(e)})
            except Exception:
                pass
        finally:
            streamer.close()

    finally:
        db.close()


@router.websocket("/inspections/live")
async def inspection_events(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    """Subscribe to inspection result events across all cameras.

    Authentication via ?token=<jwt>
    Messages: {"type": "connected"} then {"type": "inspection.created", inspection: {...}}
    plus periodic {"type": "heartbeat", timestamp}.
    """
    db = SessionLocal()
    try:
        user = await authenticate_ws(token, db)
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
            return

        await websocket.accept()
        await websocket.send_json({"type": "connected", "timestamp": time.time()})
        queue = event_bus.subscribe(TOPIC_INSPECTIONS)

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
            event_bus.unsubscribe(TOPIC_INSPECTIONS, queue)

    finally:
        db.close()
