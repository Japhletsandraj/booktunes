"""Recommendation schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.book import BookSummary


class FactorContribution(BaseModel):
    """One of the seven scoring factors behind a match score."""

    name: str
    score: float = Field(..., ge=0, le=100, description="Raw factor score, 0-100")
    weight: float = Field(..., ge=0, le=1, description="Share of the final score")
    contribution: float = Field(..., description="score * weight")
    explanation: str


class RecommendationOut(BaseModel):
    book: BookSummary
    match_score: float = Field(..., ge=0, le=100)
    confidence_score: float = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "How much evidence backs this match. Low for new users with little "
            "history — the match score alone doesn't convey that."
        ),
    )
    factors: list[FactorContribution] = Field(default_factory=list)
    reason: str | None = None
    playlist_available: bool = False
    generated_at: datetime | None = None


class PersonalizedSummary(BaseModel):
    """Natural-language explanation of why a book suits a user."""

    book_id: uuid.UUID
    user_id: uuid.UUID
    summary: str
    match_score: float
    confidence_score: float
    factors: list[FactorContribution]
    generated_at: datetime


class RecommendationFeedbackIn(BaseModel):
    book_id: uuid.UUID
    feedback_type: Literal["like", "dislike", "not_interested", "already_read"]
    reason: str | None = Field(None, max_length=500)


class MoodRecommendationParams(BaseModel):
    limit: int = Field(20, ge=1, le=50)
    include_playlist: bool = True


class RecommendationBatch(BaseModel):
    items: list[RecommendationOut]
    generated_at: datetime
    cached: bool = False
    strategy: dict[str, float] = Field(
        default_factory=dict,
        description="Blend actually used, e.g. {'collaborative': 0.3, 'content': 0.7}",
    )
