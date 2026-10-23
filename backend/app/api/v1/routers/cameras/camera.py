"""Camera REST API endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.schemas.camera import CameraCreate, CameraListOut, CameraOut, CameraUpdate
from app.db.session import get_db
from app.db.models import Camera, ProductionLine  # Fixed import, User

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.get("", response_model=List[CameraListOut], status_code=status.HTTP_200_OK)
def list_cameras(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Camera).all()


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check production line exists if line_id provided
    if payload.line_id:
        line = db.query(ProductionLine).filter(ProductionLine.id == payload.line_id).first()
        if not line:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Production line not found")
    cam = Camera(**payload.model_dump(exclude_none=True))
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return cam


@router.get("/{camera_id}", response_model=CameraOut, status_code=status.HTTP_200_OK)
def get_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    return cam


@router.put("/{camera_id}", response_model=CameraOut, status_code=status.HTTP_200_OK)
def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(cam, k, v)
    db.commit()
    db.refresh(cam)
    return cam


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    db.delete(cam)
    db.commit()
    return {"detail": "deleted"}