"""Registered background task functions.

Every function here is executable by both task-queue transports (in-process
pool and the standalone RQ worker) — the same code runs regardless of where it
is scheduled. Tasks must be importable module-level functions with
JSON-serializable arguments (they cross the Redis queue in worker mode).
"""

from __future__ import annotations

import logging

from app.services.task_queue import register_task

logger = logging.getLogger(__name__)


@register_task("video_inspection")
def run_video_inspection(camera_id: int, file_path: str, frame_interval: int = 1, max_frames: int | None = None) -> dict:
    """Inspect a video file for a camera and return the aggregated result dict.

    Loads the camera fresh from the database (queue arguments must be
    serializable primitives). Returns the same payload shape the synchronous
    ``POST /cameras/{id}/inspect-video`` endpoint returns so callers can treat
    both paths identically.
    """
    from dataclasses import asdict

    from app.db.models import Camera
    from app.db.session import SessionLocal
    from app.services.video_inspection import inspect_video_file

    db = SessionLocal()
    try:
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise ValueError(f"Camera {camera_id} not found for video_inspection task")
        result = inspect_video_file(camera, file_path, frame_interval=frame_interval, max_frames=max_frames)
        return asdict(result)
    finally:
        db.close()
