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
    # In cookie mode this may be empty: the refresh token rides in the
    # httpOnly iv_refresh cookie (path-scoped to /auth/refresh).
    refresh_token: str = ""


class TokenRefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    user_id: int
    # "body" = tokens in body (default); "cookie" = tokens in httpOnly cookies
    # and the body fields are empty strings. Clients pick their storage
    # strategy from this field.
    token_storage: str = "body"


class UserRead(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool


class LoginResponse(BaseModel):
    token: Token
    user: UserRead
    refresh_token: str
    token_storage: str = "body"
