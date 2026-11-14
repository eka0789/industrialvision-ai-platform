"""Camera REST API endpoints including session management."""

from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.schemas.camera import (
    CameraCreate,
    CameraListOut,
    CameraOut,
    CameraUpdate,
    SessionCreate,
    SessionOut,
    SessionStatusOut,
    VideoInspectionRequest,
    VideoInspectionResultOut,
)
from app.db.session import get_db
from app.db.models import Camera, InspectionSession, ModelVersion, ProductionLine, User
from app.core.constants import CameraSourceType, CameraStatus, Role
from app.services.camera_sources import redact_rtsp_url
from app.services.tenant import scoped_one, scoped_query, stamp_org

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _get_camera_or_404(camera_id: int, db: Session, current_user: User) -> Camera:
    """Tenant-scoped camera fetch: another org's id is simply 404."""
    cam = scoped_one(db, Camera, current_user, camera_id)
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    return cam


# ── Camera CRUD ──────────────────────────────────────────────────────────────


@router.get("", response_model=List[CameraListOut], status_code=status.HTTP_200_OK)
def list_cameras(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
):
    return scoped_query(db, Camera, current_user).all()


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN)),
):
    if payload.line_id:
        line = scoped_query(db, ProductionLine, current_user).filter(ProductionLine.id == payload.line_id).first()
        if not line:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Production line not found")
    if payload.source_type not in (
        CameraSourceType.SIMULATED,
        CameraSourceType.LOCAL_FILE,
        CameraSourceType.WEBCAM,
        CameraSourceType.RTSP,
        CameraSourceType.UPLOAD,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid source_type. Allowed: {[CameraSourceType.SIMULATED, CameraSourceType.LOCAL_FILE, CameraSourceType.WEBCAM, CameraSourceType.RTSP]}",
        )
    cam = Camera(**payload.model_dump(exclude_none=True))
    cam.status = CameraStatus.DISCONNECTED
    stamp_org(cam, current_user)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return cam


@router.get("/{camera_id}", response_model=CameraOut, status_code=status.HTTP_200_OK)
def get_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
):
    return _get_camera_or_404(camera_id, db, current_user)


@router.put("/{camera_id}", response_model=CameraOut, status_code=status.HTTP_200_OK)
def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN)),
):
    cam = _get_camera_or_404(camera_id, db, current_user)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(cam, k, v)
    db.commit()
    db.refresh(cam)
    return cam


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN)),
):
    cam = _get_camera_or_404(camera_id, db, current_user)
    db.delete(cam)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Camera lifecycle: start/stop (updates status, no actual stream) ────────


@router.post("/{camera_id}/start", response_model=CameraOut, status_code=status.HTTP_200_OK)
def start_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR)),
):
    """Mark camera as connected/active (stream initiation via WebSocket)."""
    cam = _get_camera_or_404(camera_id, db, current_user)
    if not cam.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera is not active")
    cam.status = CameraStatus.CONNECTED
    cam.last_error = None
    db.commit()
    db.refresh(cam)
    return cam


@router.post("/{camera_id}/stop", response_model=CameraOut, status_code=status.HTTP_200_OK)
def stop_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR)),
):
    """Mark camera as disconnected."""
    cam = _get_camera_or_404(camera_id, db, current_user)
    cam.status = CameraStatus.DISCONNECTED
    db.commit()
    db.refresh(cam)
    return cam


# ── Sessions ──────────────────────────────────────────────────────────────────


@router.post("/{camera_id}/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    camera_id: int,
    payload: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR)),
):
    """Start a new inspection session for a camera."""
    cam = _get_camera_or_404(camera_id, db, current_user)
    if not cam.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera is not active")
    mv = scoped_query(db, ModelVersion, current_user).filter(ModelVersion.id == payload.model_version_id).first()
    if not mv:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ModelVersion not found")

    session = InspectionSession(
        camera_id=camera_id,
        model_version_id=payload.model_version_id,
        status="running",
    )
    stamp_org(session, current_user)
    db.add(session)
    cam.status = CameraStatus.CONNECTED
    cam.last_error = None
    db.commit()
    db.refresh(session)
    return session


@router.get("/{camera_id}/sessions", response_model=List[SessionOut], status_code=status.HTTP_200_OK)
def list_sessions(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
):
    _get_camera_or_404(camera_id, db, current_user)
    return db.query(InspectionSession).filter(InspectionSession.camera_id == camera_id).all()


@router.get("/{camera_id}/sessions/{session_id}", response_model=SessionStatusOut, status_code=status.HTTP_200_OK)
def get_session_status(
    camera_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
):
    _get_camera_or_404(camera_id, db, current_user)
    session = db.query(InspectionSession).filter(
        InspectionSession.id == session_id,
        InspectionSession.camera_id == camera_id,
    ).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return SessionStatusOut(
        session_id=session.id,
        camera_id=session.camera_id,
        model_version_id=session.model_version_id,
        status=session.status,
        start_time=session.start_time.isoformat() if session.start_time else None,
        end_time=session.end_time.isoformat() if session.end_time else None,
        metrics=None,  # runtime metrics from CaptureWorker when stream active
    )


@router.post("/{camera_id}/sessions/{session_id}/stop", response_model=SessionOut, status_code=status.HTTP_200_OK)
def stop_session(
    camera_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR)),
):
    """Stop an active inspection session."""
    _get_camera_or_404(camera_id, db, current_user)
    session = db.query(InspectionSession).filter(
        InspectionSession.id == session_id,
        InspectionSession.camera_id == camera_id,
    ).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.status not in ("running",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is already {session.status}",
        )
    session.status = "stopped"
    session.end_time = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    db.refresh(session)
    return session


# ── Video file inspection ─────────────────────────────────────────────────────


@router.post("/{camera_id}/inspect-video", response_model=VideoInspectionResultOut, status_code=status.HTTP_200_OK)
def inspect_video(
    camera_id: int,
    payload: VideoInspectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
):
    """Run video file inspection on an existing server-side file."""
    cam = _get_camera_or_404(camera_id, db, current_user)

    from app.services.video_inspection import VideoInspector
    inspector = VideoInspector(
        cam,
        payload.file_path,
        payload.frame_interval,
        payload.max_frames,
    )
    result = inspector.run()

    # Persist last_inspection_at
    cam.last_inspection_at = datetime.datetime.now(datetime.timezone.utc)
    if result.errors:
        cam.last_error = "; ".join(result.errors[:3])
    if result.fps:
        cam.fps_input = result.fps
    db.commit()

    return VideoInspectionResultOut(
        camera_id=result.camera_id,
        total_frames=result.total_frames,
        processed_frames=result.processed_frames,
        dropped_frames=result.dropped_frames,
        fps=result.fps,
        avg_latency_ms=result.avg_latency_ms,
        duration_sec=result.duration_sec,
        detections=result.detections,
        errors=result.errors,
        metrics=result.metrics,
    )
