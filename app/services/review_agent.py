from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewReport
from app.providers.llm.protocol import LLMTokenUsage
from app.services.ai_review_service import AIReviewService


class AgentRole(StrEnum):
    GENERAL = "general"
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"


@dataclass(frozen=True, slots=True)
class AgentReviewResult:
    role: AgentRole
    report: ReviewReport
    usage: LLMTokenUsage | None = None


@runtime_checkable
class ReviewAgent(Protocol):
    @property
    def role(self) -> AgentRole: ...

    @property
    def prompt_version(self) -> str: ...

    def review(self, context: ReviewContext) -> AgentReviewResult: ...


class GeneralReviewAgent:
    """Adapts the existing single-review service to the agent contract."""

    def __init__(self, review_service: AIReviewService) -> None:
        self._review_service = review_service

    @property
    def role(self) -> AgentRole:
        return AgentRole.GENERAL

    @property
    def prompt_version(self) -> str:
        return self._review_service.prompt_version

    def review(self, context: ReviewContext) -> AgentReviewResult:
        result = self._review_service.review_with_metadata(context)
        return AgentReviewResult(
            role=self.role,
            report=result.report,
            usage=result.usage,
        )


class SpecialistReviewAgent:
    """Runs one category-focused review service for evaluation experiments."""

    def __init__(
        self,
        *,
        role: AgentRole,
        review_service: AIReviewService,
    ) -> None:
        if role == AgentRole.GENERAL:
            raise ValueError("A specialist review agent cannot use the general role")
        self._role = role
        self._review_service = review_service

    @property
    def role(self) -> AgentRole:
        return self._role

    @property
    def prompt_version(self) -> str:
        return self._review_service.prompt_version

    def review(self, context: ReviewContext) -> AgentReviewResult:
        result = self._review_service.review_with_metadata(context)
        return AgentReviewResult(
            role=self.role,
            report=result.report,
            usage=result.usage,
        )
