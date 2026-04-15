from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScoreResult(BaseModel):
    score: int
    tier: str
    summary: str
    components: dict[str, int] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    version: str = "profile_v1"
    confidence: str = "medium"
    data_gaps: list[str] = Field(default_factory=list)
    eligibility: dict[str, Any] = Field(default_factory=dict)
