from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport
from app.services.ai_review_service import AIReviewService


class AgentRole(StrEnum):
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class AgentReviewResult:
    role: AgentRole
    report: ReviewReport


@runtime_checkable
class ReviewAgent(Protocol):
    @property
    def role(self) -> AgentRole: ...

    def review(self, context: ReviewContext) -> ReviewReport: ...


class GeneralReviewAgent:
    """Adapts the existing single-review service to the agent contract."""

    def __init__(self, review_service: AIReviewService) -> None:
        self._review_service = review_service

    @property
    def role(self) -> AgentRole:
        return AgentRole.GENERAL

    def review(self, context: ReviewContext) -> ReviewReport:
        return self._review_service.review(context)
