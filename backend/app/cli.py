"""Interactive administrative commands. Run from ``backend/`` after migrations."""

from __future__ import annotations

import argparse
import getpass

from sqlalchemy.orm import Session

from app.core.constants import Role as RoleNames
from app.db.models import Role, User
from app.db.session import SessionLocal


def provision_initial_admin(db: Session, username: str, password: str) -> User:
    """Create the first administrator without embedding credentials in code."""
    if len(username) < 3 or len(username) > 80:
        raise ValueError("Username must contain between 3 and 80 characters")
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Administrator password must contain between 12 and 128 characters")
    if db.query(User).filter(User.username == username).first():
        raise ValueError("Username already exists")
    if db.query(User).filter(User.role.has(name=RoleNames.ADMIN)).first():
        raise ValueError("An administrator already exists; use the admin user-management API")

    role = db.query(Role).filter(Role.name == RoleNames.ADMIN).first()
    if role is None:
        role = Role(name=RoleNames.ADMIN, description="Administrator role")
        db.add(role)
        db.flush()

    user = User(username=username, hashed_password="", role_id=role.id, is_active=True)
    user.set_password(password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    parser = argparse.ArgumentParser(prog="industrialvision-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin = subparsers.add_parser("create-admin", help="Interactively provision the first admin")
    create_admin.add_argument("--username", required=True)
    args = parser.parse_args()

    password = getpass.getpass("New administrator password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match")

    db = SessionLocal()
    try:
        user = provision_initial_admin(db, args.username, password)
    except Exception as exc:
        db.rollback()
        parser.error(str(exc))
    finally:
        db.close()
    print(f"Created administrator account {user.username!r} (id={user.id}).")


if __name__ == "__main__":
    main()
