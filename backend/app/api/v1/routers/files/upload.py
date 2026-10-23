"""File upload endpoints for inspection evidence."""

from __future__ import annotations

import os
import uuid
from typing import List

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.v1.deps import get_db, get_current_user
from app.db.session import SessionLocal
from app.db.models import Inspection, DetectionResult, AnomalyResult
from app.schemas.inspection import InspectionOut

router = APIRouter(prefix="/files", tags=["files"])

# Evidence storage directory
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "..", "evidence")
os.makedirs(EVIDENCE_DIR, exist_ok=True)


def _save_evidence(file: UploadFile, inspection_id: int) -> str:
    """Save uploaded evidence file with unique name."""
    ext = os.path.splitext(file.filename)[1] if file.filename else ".bin"
    filename = f"inspection_{inspection_id}_{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(EVIDENCE_DIR, filename)
    with open(filepath, "wb") as f:
        content = file.file.read()
        f.write(content)
    return filename


@router.post("/upload/{inspection_id}", response_model=InspectionOut, status_code=status.HTTP_200_OK)
def upload_evidence(
    inspection_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload evidence file for an inspection."""
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if not inspection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found"
        )

    # Verify user has access to this inspection's camera
    camera = db.query(inspection.Camera).first()  # type: ignore
    if camera.user_id != current_user.id and current_user.role.name != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to upload evidence for this inspection",
        )

    filename = _save_evidence(file, inspection_id)

    # Update inspection evidence path
    from sqlalchemy import update as sa_update
    db.execute(
        sa_update(Inspection).where(Inspection.id == inspection_id).values(
            evidence_path=f"/evidence/{filename}"
        )
    )
    db.commit()
    db.refresh(inspection)

    return inspection