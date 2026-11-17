"""Standard Food Division SOP documents for Knowledge AI / RAG (Fase 6).

Re-exports `FOOD_SOP_SEED_DOCUMENTS` and `seed_food_safety_sops` from
`app.rag.food_sop_seed` for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.rag.food_sop_seed import FOOD_SOP_SEED_DOCUMENTS as FOOD_SOP_DOCUMENTS
from app.rag.food_sop_seed import seed_food_safety_sops


def ensure_food_sops_ingested(db: Session, org_id: int = 1, user_id: int | None = None) -> list[dict[str, Any]]:
    """Idempotently store and ingest the bundled Food Division SOP documents into RAG."""
    return seed_food_safety_sops(db=db, org_id=org_id, user_id=user_id)
