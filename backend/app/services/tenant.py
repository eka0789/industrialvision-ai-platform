"""Tenant isolation helpers (A6 multi-tenancy).

Every tenant-scoped query goes through :func:`scoped_query` so the org filter
cannot be forgotten at a call site; every creation goes through
:func:`stamp_org` so new rows always carry the caller's org. Cross-tenant
access is denied by construction (the org predicate is part of the query, so a
foreign id simply matches nothing) and asserted by router tests.
"""

from __future__ import annotations

from sqlalchemy import Column
from sqlalchemy.orm import Query, Session

from app.db.models import Organization, User

BOOTSTRAP_ORG_ID = 1


def get_user_org_id(user: User) -> int:
    """Resolve the caller's organization (bootstrap users default to org 1)."""
    org_id = getattr(user, "org_id", None)
    return int(org_id) if org_id is not None else BOOTSTRAP_ORG_ID


def get_or_create_default_org(db: Session) -> Organization:
    """Return the bootstrap organization, creating it if missing (seeds/CLI)."""
    org = db.get(Organization, BOOTSTRAP_ORG_ID)
    if org is None:
        org = Organization(
            id=BOOTSTRAP_ORG_ID,
            name="Default Organization",
            slug="default",
        )
        db.add(org)
        db.flush()
    return org


def scoped_query(db: Session, model: type, user: User) -> Query:
    """Return a query on ``model`` restricted to the caller's organization.

    The org predicate is ANDed with whatever the caller adds next, so
    ``scoped_query(db, Camera, user).filter(Camera.id == x)`` can never return
    another org's row.
    """
    org_id = get_user_org_id(user)
    return db.query(model).filter(model.org_id == org_id)  # type: ignore[attr-defined]


def scoped_one(db: Session, model: type, user: User, row_id: int):
    """Fetch a single tenant-scoped row by primary key (None when foreign)."""
    return scoped_query(db, model, user).filter(model.id == row_id).first()  # type: ignore[attr-defined]


def stamp_org(row, user: User) -> None:
    """Assign the caller's org to a new row before it is added to a session."""
    row.org_id = get_user_org_id(user)  # type: ignore[attr-defined]
