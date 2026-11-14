"""RAG ingestion pipeline (C2): extract → chunk → embed → store.

Runs as a registered background task (``rag_ingest_document``) on the same
task-queue transports as every other background job. Ingestion is
all-or-nothing per document: any failure after validation marks the document
``failed`` with a structured reason and leaves ZERO chunks (no partial
documents — asserted in tests).

Extraction support: TXT/MD/CSV (stdlib), PDF (``pypdf`` optional), DOCX
(``python-docx`` optional). Missing optional dependency → structured
failure naming the extra to install; the document is failed, not fabricated.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging

from app.core.config import settings
from app.db.models import RagChunk, RagDocument
from app.rag.embeddings import get_embedding_provider

logger = logging.getLogger(__name__)

ALLOWED_MIME = {
    "text/plain": [".txt", ".md", ".log"],
    "text/csv": [".csv"],
    "application/pdf": [".pdf"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
}


class IngestionError(RuntimeError):
    """Structured ingestion failure (reason surfaces on the document row)."""


def validate_upload(filename: str, mime: str, content: bytes) -> str:
    """Reuse upload-hardening rules: type, extension, size, signature.

    Returns the safe file extension. Raises IngestionError otherwise.
    """
    mime = (mime or "").split(";")[0].strip().lower()
    extensions = ALLOWED_MIME.get(mime)
    if extensions is None:
        raise IngestionError(f"Unsupported document type: {mime or 'unknown'}")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in extensions:
        raise IngestionError(f"Extension {suffix or '(none)'} does not match type {mime}")
    if len(content) > settings.RAG_MAX_UPLOAD_BYTES:
        raise IngestionError(f"Document exceeds {settings.RAG_MAX_UPLOAD_BYTES} byte limit")
    if not content:
        raise IngestionError("Empty document")
    # Signature checks (magic bytes) where they exist.
    if mime == "application/pdf" and not content.startswith(b"%PDF-"):
        raise IngestionError("File is not a valid PDF (bad signature)")
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" and not content.startswith(b"PK"):
        raise IngestionError("File is not a valid DOCX (bad signature)")
    if mime.startswith("text/") and b"\x00" in content[:4096]:
        raise IngestionError("Text document contains binary data")
    return suffix


def extract_text(content: bytes, mime: str, suffix: str) -> list[tuple[str | None, str]]:
    """Return (page, text) sections. TXT/CSV: one section; PDF: per page."""
    mime = (mime or "").split(";")[0].strip().lower()
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise IngestionError("PDF support requires the optional 'pypdf' package") from exc
        try:
            reader = PdfReader(io.BytesIO(content))
            sections = [(index + 1, (page.extract_text() or "").strip()) for index, page in enumerate(reader.pages)]
        except Exception as exc:  # noqa: BLE001 - corrupt PDF → structured failure
            raise IngestionError(f"PDF could not be parsed: {exc}") from exc
        return [section for section in sections if section[1]]
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            import docx  # type: ignore[import-not-found]

            document = docx.Document(io.BytesIO(content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        except ImportError as exc:
            raise IngestionError("DOCX support requires the optional 'python-docx' package") from exc
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(f"DOCX could not be parsed: {exc}") from exc
        return [(None, text)] if text.strip() else []
    if mime == "text/csv":
        try:
            decoded = content.decode("utf-8-sig")
            rows = list(csv.reader(io.StringIO(decoded)))
            text = "\n".join(" | ".join(cell.strip() for cell in row) for row in rows if any(cell.strip() for cell in row))
        except UnicodeDecodeError as exc:
            raise IngestionError("CSV is not valid UTF-8") from exc
        return [(None, text)] if text.strip() else []
    # Plain text family.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError("Text document is not valid UTF-8") from exc
    return [(None, text)] if text.strip() else []


def chunk_sections(sections: list[tuple[str | None, str]]) -> list[dict]:
    """Fixed-size overlapping chunks with page tracking (documented sizes)."""
    size = settings.RAG_CHUNK_SIZE
    overlap = min(settings.RAG_CHUNK_OVERLAP, size - 1)
    chunks: list[dict] = []
    for page, text in sections:
        normalized = " ".join(text.split())
        start = 0
        ordinal_offset = len(chunks)
        while start < len(normalized):
            piece = normalized[start : start + size]
            chunks.append({"ordinal": ordinal_offset + len([c for c in chunks if c["page"] == page]), "page": page, "content": piece})
            if start + size >= len(normalized):
                break
            start += size - overlap
    return chunks


def ingest_document(db, document_id: int) -> dict:
    """Task entry point: process one pending document end-to-end.

    All-or-nothing: chunks are only committed together with the READY status.
    """
    document = db.get(RagDocument, document_id)
    if document is None:
        raise IngestionError(f"document {document_id} not found")
    try:
        document.status = "processing"
        db.commit()

        content = _read_original(document)
        sections = extract_text(content, document.mime_type, "")
        chunks = chunk_sections(sections)
        if not chunks:
            raise IngestionError("no extractable text found in document")

        provider = get_embedding_provider()
        vectors = provider.embed([chunk["content"] for chunk in chunks])
        if len(vectors.vectors) != len(chunks):
            raise IngestionError("embedding provider returned an unexpected vector count")
        if document.embedding_dim is not None and document.embedding_dim != vectors.dim:
            raise IngestionError(
                f"embedding dim mismatch: document was ingested with dim {document.embedding_dim}, provider returned {vectors.dim}"
            )

        rows = []
        for chunk, vector in zip(chunks, vectors.vectors):
            rows.append(
                RagChunk(
                    org_id=document.org_id,
                    document_id=document.id,
                    ordinal=chunk["ordinal"],
                    page=chunk["page"],
                    content=chunk["content"],
                    content_sha256=hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest(),
                    embedding_json=vector,
                    embedding_model=vectors.provider,
                )
            )
        document.chunk_count = len(rows)
        document.status = "ready"
        document.error = None
        document.embedding_provider = vectors.provider
        document.embedding_dim = vectors.dim
        db.add_all(rows)  # committed together with READY → no partial document
        db.commit()
        return {"document_id": document.id, "chunks": len(rows), "provider": vectors.provider, "dim": vectors.dim}
    except Exception as exc:  # noqa: BLE001 - structured failure path
        db.rollback()
        document = db.get(RagDocument, document_id)
        if document is not None:
            document.status = "failed"
            document.error = str(exc)[:1000]
            document.chunk_count = 0
            db.commit()
        logger.warning("RAG ingestion failed for document %s: %s", document_id, exc)
        raise


def _read_original(document: RagDocument) -> bytes:
    """Read the stored original upload from disk (documents dir under EVIDENCE_ROOT)."""
    from pathlib import Path

    from app.services.evidence import EvidenceStorage

    root = Path(settings.EVIDENCE_ROOT) / "rag"
    path = root / document.filename
    if not path.is_file():
        raise IngestionError(f"stored file missing: {document.filename}")
    _ = EvidenceStorage  # keep import parity; path containment checked below
    resolved = path.resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise IngestionError("stored file path escaped the documents root")
    return resolved.read_bytes()


def store_original(org_id: int, filename: str, content: bytes) -> str:
    """Persist the uploaded original under EVIDENCE_ROOT/rag; returns the name."""
    import re
    from pathlib import Path

    root = Path(settings.EVIDENCE_ROOT) / "rag"
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-180:] or "document.bin"
    name = f"org{org_id}-{safe}"
    (root / name).write_bytes(content)
    return name
