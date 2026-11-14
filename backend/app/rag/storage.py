"""RAG vector storage adapter (C1).

Two backends behind one interface:

- ``PgVectorStore`` — PostgreSQL with the ``pgvector`` extension available:
  cosine similarity executes in SQL (``embedding_json::numeric[]`` math is
  replaced by a ``<=>`` operator query when the extension exists).
- ``NumpyJsonStore`` — everything else (SQLite dev, PostgreSQL without the
  extension): loads the org's chunk vectors (id + JSON → numpy) and computes
  cosine similarity in memory. Bounded by chunk count; documented O(n) scan.

Both only ever see rows of ONE organization (tenant isolation by parameter,
asserted in tests). ``describe()`` returns the active backend label so /health
and the monitoring page can report it honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.db.models import RagChunk


@dataclass
class VectorHit:
    chunk_id: int
    document_id: int
    score: float


class NumpyJsonStore:
    backend = "numpy-json"

    def search(
        self,
        db: Session,
        *,
        org_id: int,
        query_vector: list[float],
        limit: int,
    ) -> list[VectorHit]:
        rows = (
            db.query(RagChunk.id, RagChunk.document_id, RagChunk.embedding_json)
            .filter(RagChunk.org_id == org_id, RagChunk.embedding_json.is_not(None))
            .all()
        )
        if not rows:
            return []
        query = np.asarray(query_vector, dtype=float)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return []
        hits: list[VectorHit] = []
        for chunk_id, document_id, raw in rows:
            vector = np.asarray(raw, dtype=float)
            norm = np.linalg.norm(vector)
            if norm == 0:
                continue
            score = float(np.dot(query, vector) / (query_norm * norm))
            hits.append(VectorHit(chunk_id=int(chunk_id), document_id=int(document_id), score=score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]


class PgVectorStore(NumpyJsonStore):
    """pgvector-backed cosine search (PostgreSQL + extension available).

    Vectors are written as JSON (same column) and cast on the fly; at larger
    scale a dedicated vector column + HNSW index is the documented upgrade —
    this adapter already isolates the SQL difference from callers.
    """

    backend = "pgvector"

    def search(
        self,
        db: Session,
        *,
        org_id: int,
        query_vector: list[float],
        limit: int,
    ) -> list[VectorHit]:
        try:
            vector_literal = "[" + ",".join(f"{value:.7f}" for value in query_vector) + "]"
            rows = db.execute(
                sa_text(
                    "SELECT id, document_id, 1 - (embedding_json::vector <=> :q) AS score "
                    "FROM rag_chunks WHERE org_id = :org AND embedding_json IS NOT NULL "
                    "ORDER BY embedding_json::vector <=> :q LIMIT :limit"
                ),
                {"q": vector_literal, "org": org_id, "limit": limit},
            ).all()
            return [VectorHit(chunk_id=int(r[0]), document_id=int(r[1]), score=float(r[2])) for r in rows]
        except Exception:  # noqa: BLE001 - extension missing/removed → numpy path
            return super().search(db, org_id=org_id, query_vector=query_vector, limit=limit)


def sa_text(sql: str):
    from sqlalchemy import text as sql_text

    return sql_text(sql)


def pgvector_available(db: Session) -> bool:
    """True only on PostgreSQL with the vector extension installed."""
    if db.get_bind().dialect.name != "postgresql":
        return False
    try:
        row = db.execute(sa_text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def get_vector_store(db: Session) -> NumpyJsonStore | PgVectorStore:
    return PgVectorStore() if pgvector_available(db) else NumpyJsonStore()


def describe_backend(db: Session) -> dict[str, Any]:
    store = get_vector_store(db)
    return {"backend": store.backend, "pgvector": store.backend == "pgvector"}
