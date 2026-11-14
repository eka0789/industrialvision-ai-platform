"""Hybrid retrieval (C4): BM25-lite keyword + vector cosine, RRF fusion.

Retrieval-empty is a first-class outcome: ``retrieve()`` returns an empty list
and the answer service must REFUSE rather than fabricate (test-enforced).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import RagChunk
from app.rag.embeddings import get_embedding_provider
from app.rag.storage import get_vector_store


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    content: str
    page: int | None
    score: float
    sources: set[str] = field(default_factory=set)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25Lite:
    """Minimal BM25 (k1=1.5, b=0.75) over one org's chunks — stdlib only."""

    k1 = 1.5
    b = 0.75

    def __init__(self, docs: dict[int, list[str]]) -> None:
        self.docs = docs
        self.doc_len = {cid: len(tokens) for cid, tokens in docs.items()}
        self.avg_len = (sum(self.doc_len.values()) / len(docs)) if docs else 0.0
        self.df: dict[str, int] = {}
        self.tf: dict[int, dict[str, int]] = {}
        for cid, tokens in docs.items():
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            self.tf[cid] = counts
            for token in counts:
                self.df[token] = self.df.get(token, 0) + 1

    def idf(self, token: str) -> float:
        n = len(self.docs)
        df = self.df.get(token, 0)
        return math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list[str], cid: int) -> float:
        if not self.avg_len:
            return 0.0
        total = 0.0
        norm = self.doc_len.get(cid, 0) / self.avg_len
        counts = self.tf.get(cid, {})
        for token in query_tokens:
            freq = counts.get(token, 0)
            if not freq:
                continue
            total += (
                self.idf(token)
                * freq
                * (self.k1 + 1)
                / (freq + self.k1 * (1 - self.b + self.b * norm))
            )
        return total


def retrieve(
    db: Session,
    *,
    org_id: int,
    query: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval restricted to one org; empty list when nothing matches."""
    from app.core.config import settings

    top_k = top_k or settings.RAG_TOP_K
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    rows = (
        db.query(RagChunk)
        .filter(RagChunk.org_id == org_id)
        .order_by(RagChunk.document_id, RagChunk.ordinal)
        .limit(5000)  # bounded scan; documented for the numpy backend
        .all()
    )
    if not rows:
        return []

    # Keyword leg (BM25-lite).
    bm25 = BM25Lite({row.id: _tokenize(row.content) for row in rows})
    keyword_scores: dict[int, float] = {}
    for chunk_id in bm25.docs:
        value = bm25.score(query_tokens, chunk_id)
        if value > 0:
            keyword_scores[chunk_id] = value

    # Vector leg.
    provider = get_embedding_provider()
    vector = provider.embed([query]).vectors[0]
    store = get_vector_store(db)
    vector_hits = store.search(db, org_id=org_id, query_vector=vector, limit=top_k * 4)

    # Reciprocal Rank Fusion (k=60).
    def rrf(rank: int) -> float:
        return 1.0 / (60.0 + rank)

    fused: dict[int, float] = {}
    for rank, chunk_id in enumerate(sorted(keyword_scores, key=keyword_scores.get, reverse=True)[: top_k * 4]):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + rrf(rank)
    for rank, hit in enumerate(vector_hits):
        fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + rrf(rank)

    by_id = {row.id: row for row in rows}
    results: list[RetrievedChunk] = []
    for chunk_id, score in sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]:
        row = by_id.get(chunk_id)
        if row is None:
            continue
        sources = set()
        if chunk_id in keyword_scores:
            sources.add("keyword")
        if any(hit.chunk_id == chunk_id for hit in vector_hits):
            sources.add("vector")
        results.append(
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                page=row.page,
                score=round(score, 6),
                sources=sources,
            )
        )
    return results
