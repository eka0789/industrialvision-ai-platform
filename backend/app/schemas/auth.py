"""Authentication / token schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


class TokenPayload(BaseModel):
    sub: str
    role: str
    user_id: int
    exp: int


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserRead(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool


class LoginResponse(BaseModel):
    token: Token
    user: UserRead
