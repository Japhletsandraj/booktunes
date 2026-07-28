"""Auth and user-profile schemas."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel

ReadingLevel = Literal["beginner", "intermediate", "advanced"]

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    full_name: str | None = Field(None, max_length=120)
    reading_level: ReadingLevel | None = None

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username may contain only letters, numbers, dots, hyphens and "
                "underscores (3-30 characters)."
            )
        return v.lower()

    @field_validator("password")
    @classmethod
    def _strong_password(cls, v: str) -> str:
        # bcrypt truncates past 72 *bytes*, so guard bytes not characters.
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes.")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit.")
        return v


class UserLogin(BaseModel):
    # Accepts either username or email in the same field.
    identifier: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: EmailStr
    full_name: str | None = None
    avatar_url: str | None = None
    reading_level: ReadingLevel | None = None
    join_date: datetime | None = None
    last_active: datetime | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=120)
    avatar_url: str | None = None
    reading_level: ReadingLevel | None = None


class PreferenceQuiz(BaseModel):
    """Cold-start signal collected at signup.

    Seeds the user's preference embedding before any interaction exists, which
    is what keeps a brand-new account from getting purely popularity-based
    recommendations.
    """

    favorite_genres: list[str] = Field(..., min_length=1, max_length=10)
    favorite_authors: list[str] = Field(default_factory=list, max_length=10)
    preferred_moods: list[str] = Field(default_factory=list, max_length=10)
    reading_level: ReadingLevel | None = None
    preferred_length: Literal["short", "medium", "long", "any"] | None = "any"
    music_genres: list[str] = Field(default_factory=list, max_length=10)
    books_per_month: int | None = Field(None, ge=0, le=100)
