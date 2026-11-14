"""Embedding provider abstraction (C3).

Providers, in selection order:

1. ``openai`` — when ``OPENAI_API_KEY`` is set and the ``openai`` package is
   importable (``text-embedding-3-small``, 1536 dims).
2. ``sentence-transformers`` — when the optional extra is installed and
   ``RAG_EMBEDDING_PROVIDER=sentence-transformers`` is requested explicitly.
3. ``local-hashing`` (default, always available) — deterministic feature
   hashing (documented: dim 384, unigram+bigram hashing with sign trick).
   NOT semantically equivalent to a neural embedding; it supports exact-ish
   keyword-adjacent recall in tests and offline deployments and is always
   labeled ``local-hashing`` in API responses.

The adapter self-reports ``provider`` and ``dim`` on every response. A stored
chunk whose dim differs from the active provider is rejected at ingest time
(migration-safe dim mismatch handling).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings

LOCAL_HASH_DIM = 384


@dataclass
class EmbeddingResponse:
    vectors: list[list[float]]
    provider: str
    dim: int


class EmbeddingProvider(Protocol):
    provider: str
    dim: int

    def embed(self, texts: list[str]) -> EmbeddingResponse: ...


class LocalHashingEmbedder:
    """Deterministic hashed bag-of-features embedding (dim 384).

    Documented properties: deterministic for identical text, L2-normalized,
    sensitive to unigrams and bigrams, zero external dependencies. This is a
    fallback, not a semantic model.
    """

    provider = "local-hashing"
    dim = LOCAL_HASH_DIM

    def embed(self, texts: list[str]) -> EmbeddingResponse:
        vectors = [self._one(text) for text in texts]
        return EmbeddingResponse(vectors=vectors, provider=self.provider, dim=self.dim)

    def _one(self, text: str) -> list[float]:
        tokens = _tokenize(text)
        counts: dict[int, float] = {}
        for i, token in enumerate(tokens):
            self._bump(counts, token, 1.0)
            if i + 1 < len(tokens):
                self._bump(counts, f"{token}_{tokens[i + 1]}", 0.5)
        if not counts:
            # All-noise input still yields a valid, deterministic unit vector.
            self._bump(counts, "__empty__", 1.0)
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        return [counts.get(index, 0.0) / norm for index in range(self.dim)]

    def _bump(self, counts: dict[int, float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % self.dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        counts[index] = counts.get(index, 0.0) + sign * weight


def _tokenize(text: str) -> list[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if token]


class OpenAIEmbedder:
    """OpenAI text-embedding-3-small; requires the key + optional dependency."""

    provider = "openai"
    dim = 1536

    def __init__(self) -> None:
        try:
            from openai import OpenAI  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional path
            raise RuntimeError("openai package not installed") from exc
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")

    def embed(self, texts: list[str]) -> EmbeddingResponse:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.embeddings.create(model="text-embedding-3-small", input=texts)
        vectors = [item.embedding for item in response.data]
        return EmbeddingResponse(vectors=vectors, provider=self.provider, dim=self.dim)


class SentenceTransformerEmbedder:
    """Optional sentence-transformers extra (explicit provider selection only)."""

    provider = "sentence-transformers"
    dim = -1  # resolved on first use

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional path
            raise RuntimeError("sentence-transformers package not installed") from exc
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> EmbeddingResponse:
        vectors = self._model.encode(texts, normalize_embeddings=True).tolist()
        return EmbeddingResponse(vectors=vectors, provider=self.provider, dim=self.dim)


def get_embedding_provider(requested: str | None = None) -> EmbeddingProvider:
    """Select a provider; the labeled local fallback is always available."""
    requested = (requested or settings.RAG_EMBEDDING_PROVIDER or "auto").casefold()
    if requested == "local-hashing":
        return LocalHashingEmbedder()
    if requested == "sentence-transformers":
        return SentenceTransformerEmbedder()
    if requested in ("openai", "auto"):
        try:
            return OpenAIEmbedder()
        except Exception:  # noqa: BLE001 - missing key/package → fallback
            if requested == "openai":
                raise
    return LocalHashingEmbedder()
