"""Browser video streaming path (F1): MJPEG over authenticated fetch.

Design (honest tradeoffs documented in docs/OPERATIONS.md §6):

- The browser fetches ``/ws/cameras/{id}/mjpeg`` with the SAME bearer token it
  already holds (``Authorization`` header) — no tokens in URLs, no query
  params, consistent with the WS auth story.
- The response is ``multipart/x-mixed-replace`` JPEG frames produced by the
  existing bounded capture worker; detection overlays arrive separately over
  the existing WS events (the canvas player composes them client-side).
- This is MJPEG, NOT WebRTC: latency is roughly the capture interval + network
  jitter (typically 0.5–2 s LAN). Sub-second glass-to-glass latency would need
  WebRTC/HLS which is a documented future work item, not a claim made here.

Tests cover lifecycle (boundary framing), auth denial, and camera 404s.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.db.models import Camera, User
from app.db.session import get_db
from app.services.camera import CameraStreamer

router = APIRouter(prefix="/ws", tags=["streaming"])


async def _mjpeg_frames(db: Session, camera: Camera, max_fps: float) -> AsyncIterator[bytes]:
    """Yield MJPEG parts from the capture worker's latest frames."""
    streamer = CameraStreamer(camera, db)
    boundary = b"--ivframe\r\n"
    interval = 1.0 / max(0.1, max_fps)
    try:
        while True:
            frame_jpeg = await asyncio.to_thread(streamer.latest_jpeg)
            if frame_jpeg:
                yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(frame_jpeg)).encode() + b"\r\n\r\n" + frame_jpeg + b"\r\n"
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    finally:
        streamer.close()


@router.get("/cameras/{camera_id}/mjpeg")
def camera_mjpeg(
    camera_id: int,
    request: Request,
    max_fps: float = 5.0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """MJPEG stream of a camera's latest frames (auth via Authorization header)."""
    del request  # documented signature
    camera = db.query(Camera).filter(Camera.id == camera_id, Camera.org_id == user.org_id).first()
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    if not camera.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera is not active")
    max_fps = min(max(max_fps, 0.5), 30.0)
    return StreamingResponse(
        _mjpeg_frames(db, camera, max_fps),
        media_type="multipart/x-mixed-replace; boundary=ivframe",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
