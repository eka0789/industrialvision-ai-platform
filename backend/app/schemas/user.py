"""User / role schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.constants import Role


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None = None


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    role: RoleOut
    is_active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default=Role.OPERATOR, max_length=40)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=80)
    email: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
