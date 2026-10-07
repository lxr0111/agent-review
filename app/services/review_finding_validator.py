from dataclasses import dataclass

from app.domain.review.entities import ReviewContext
from app.domain.review.report import ReviewFinding
from app.services.review_agent import AgentReviewResult, AgentRole


class ReviewFindingValidationError(ValueError):
    """Raised when an agent result is unsafe to include in an aggregate report."""


@dataclass(frozen=True, slots=True)
class ValidatedAgentFinding:
    role: AgentRole
    finding: ReviewFinding


class ReviewFindingValidator:
    """Revalidates agent findings at the multi-agent aggregation boundary."""

    def validate(
        self,
        *,
        context: ReviewContext,
        result: AgentReviewResult,
    ) -> tuple[ValidatedAgentFinding, ...]:
        allowed_paths = {changed_file.path for changed_file in context.reviewable_files}
        identities: set[tuple[str, str, str]] = set()
        validated: list[ValidatedAgentFinding] = []

        for finding in result.report.findings:
            self._validate_finding(finding, allowed_paths=allowed_paths)
            identity = (
                finding.file_path,
                finding.title.casefold().strip(),
                finding.explanation.casefold().strip(),
            )
            if identity in identities:
                raise ReviewFindingValidationError(
                    f"Agent {result.role.value} returned a duplicate finding for "
                    f"{finding.file_path}"
                )
            identities.add(identity)
            validated.append(ValidatedAgentFinding(role=result.role, finding=finding))

        return tuple(validated)

    @staticmethod
    def _validate_finding(
        finding: ReviewFinding,
        *,
        allowed_paths: set[str],
    ) -> None:
        if finding.file_path not in allowed_paths:
            raise ReviewFindingValidationError(
                f"Agent finding references an unknown file: {finding.file_path}"
            )
        if not finding.title.strip() or len(finding.title) > 200:
            raise ReviewFindingValidationError(
                "Agent finding title must contain between 1 and 200 characters"
            )
        if not finding.explanation.strip():
            raise ReviewFindingValidationError("Agent finding explanation must not be empty")
        if not finding.suggestion.strip():
            raise ReviewFindingValidationError("Agent finding suggestion must not be empty")
        if not 0 <= finding.confidence <= 1:
            raise ReviewFindingValidationError("Agent finding confidence must be between 0 and 1")
