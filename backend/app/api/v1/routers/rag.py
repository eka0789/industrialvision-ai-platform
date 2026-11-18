"""Knowledge AI / RAG API (C6): document upload/manage + grounded chat.

RBAC: viewer read-only (list/chat), operator+ upload, admin delete.
Chat is rate-limited per user; uploads reuse the ingestion hardening rules.
All routes org-scoped; audit-logged state changes.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, RagDocument, User
from app.db.session import get_db
from app.rag import answers as rag_answers
from app.rag import ingestion as rag_ingestion
from app.rag.storage import describe_backend
from app.services.task_queue import task_queue
from app.services.tenant import get_user_org_id, scoped_one, scoped_query, stamp_org

router = APIRouter(prefix="/rag", tags=["knowledge"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
UPLOAD_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER)
DELETE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN)

# Simple per-user rate limit for chat (defense against automated extraction).
_CHAT_WINDOW_SECONDS = 60
_CHAT_MAX_PER_WINDOW = 12
_chat_hits: dict[int, deque[float]] = defaultdict(deque)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


def _document_or_404(db: Session, user: User, document_id: int) -> RagDocument:
    document = scoped_one(db, RagDocument, user, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.get("/documents")
def list_documents(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    rows = scoped_query(db, RagDocument, _user).order_by(RagDocument.created_at.desc()).limit(200).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "filename": row.filename,
            "status": row.status,
            "error": row.error,
            "chunk_count": row.chunk_count,
            "embedding_provider": row.embedding_provider,
            "embedding_dim": row.embedding_dim,
            "size_bytes": row.size_bytes,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/storage")
def storage_info(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> dict:
    """Active vector backend — reported honestly for the monitoring page."""
    return describe_backend(db)


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*UPLOAD_ROLES)),
) -> dict:
    """Accept a document, validate hardening rules, queue background ingestion.

    Returns 202 with the document id; the status API reports processing state.
    """
    content = await file.read(settings.RAG_MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.RAG_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Document exceeds upload limit")
    filename = file.filename or "document"
    mime = file.content_type or ""
    try:
        rag_ingestion.validate_upload(filename, mime, content)
    except rag_ingestion.IngestionError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc

    stored_name = rag_ingestion.store_original(get_user_org_id(user), filename, content)
    document = RagDocument(
        title=filename,
        filename=stored_name,
        mime_type=(mime.split(";")[0].strip().lower()),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        status="pending",
        uploaded_by_user_id=user.id,
    )
    stamp_org(document, user)
    db.add(document)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="rag.document.uploaded",
            details_json={"filename": filename, "size": len(content)},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(document)

    task_queue.enqueue("rag_ingest_document", document.id)
    return {"id": document.id, "status": document.status, "queued": True}


@router.get("/documents/{document_id}")
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> dict:
    row = _document_or_404(db, _user, document_id)
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "error": row.error,
        "chunk_count": row.chunk_count,
        "embedding_provider": row.embedding_provider,
        "embedding_dim": row.embedding_dim,
    }


@router.get("/documents/{document_id}/chunks")
def list_chunks(
    document_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    from app.db.models import RagChunk

    document = _document_or_404(db, _user, document_id)
    rows = (
        scoped_query(db, RagChunk, _user)
        .filter(RagChunk.document_id == document.id)
        .order_by(RagChunk.ordinal)
        .limit(2000)
        .all()
    )
    return [
        {
            "id": row.id,
            "ordinal": row.ordinal,
            "page": row.page,
            "content": row.content,
            "sha256": row.content_sha256,
        }
        for row in rows
    ]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*DELETE_ROLES)),
) -> Response:
    document = _document_or_404(db, user, document_id)
    db.delete(document)  # chunks cascade
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="rag.document.deleted",
            details_json={"document_id": document.id, "filename": document.filename},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat")
def chat(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> dict:
    """Grounded answer with 1:1 chunk citations; refuses when retrieval is empty."""
    now = time.monotonic()
    hits = _chat_hits[user.id]
    while hits and now - hits[0] > _CHAT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= _CHAT_MAX_PER_WINDOW:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Chat rate limit exceeded; retry shortly")
    hits.append(now)

    result = rag_answers.answer_question(db, org_id=get_user_org_id(user), question=payload.question, top_k=payload.top_k)
    return {
        "status": result.status,
        "answer": result.answer,
        "provider": result.provider,
        "citations": result.citations,
    }


@router.post("/seed-food-sops", status_code=status.HTTP_201_CREATED)
def seed_food_division_sops(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*UPLOAD_ROLES)),
) -> dict:
    """Ingest standard Food Division SOPs (HACCP Packaging Defect CCP, Washdown Camera, Heat Sealer PdM)."""
    from app.rag.food_sops import ensure_food_sops_ingested

    org_id = get_user_org_id(user)
    docs = ensure_food_sops_ingested(db, org_id=org_id, user_id=user.id)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=org_id,
            action="rag.food_sops.seeded",
            details_json={"count": len(docs)},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"status": "ok", "documents": docs}
