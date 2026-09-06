from app.domain.review.entities import ReviewContext
from app.domain.review.exceptions import ReviewOutputValidationError
from app.domain.review.report import (
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    ReviewRiskLevel,
)
from app.providers.llm.prompt_builder import PROMPT_VERSION, ReviewPromptBuilder
from app.providers.llm.protocol import LLMProvider

_SEVERITY_TO_RISK = {
    FindingSeverity.LOW: ReviewRiskLevel.LOW,
    FindingSeverity.MEDIUM: ReviewRiskLevel.MEDIUM,
    FindingSeverity.HIGH: ReviewRiskLevel.HIGH,
    FindingSeverity.CRITICAL: ReviewRiskLevel.CRITICAL,
}
_RISK_ORDER = {
    ReviewRiskLevel.NONE: 0,
    ReviewRiskLevel.LOW: 1,
    ReviewRiskLevel.MEDIUM: 2,
    ReviewRiskLevel.HIGH: 3,
    ReviewRiskLevel.CRITICAL: 4,
}


class AIReviewService:
    def __init__(
        self,
        provider: LLMProvider,
        prompt_builder: ReviewPromptBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder or ReviewPromptBuilder()

    def review(self, context: ReviewContext) -> ReviewReport:
        if not context.reviewable_files:
            return ReviewReport(
                summary="No reviewable code changes were available.",
                risk_level=ReviewRiskLevel.NONE,
                findings=(),
                model_name=None,
                prompt_version=PROMPT_VERSION,
                reviewed_file_count=0,
                skipped_file_count=len(context.skipped_files),
            )

        prompt = self._prompt_builder.build(context)
        output = self._provider.generate_review(prompt)
        allowed_paths = {changed_file.path for changed_file in context.reviewable_files}
        findings: list[ReviewFinding] = []
        identities: set[tuple[str, str, str]] = set()

        for candidate in output.findings:
            if candidate.file_path not in allowed_paths:
                raise ReviewOutputValidationError(
                    f"LLM finding references an unknown file: {candidate.file_path}"
                )
            identity = (
                candidate.file_path,
                candidate.title.casefold(),
                candidate.explanation.casefold(),
            )
            if identity in identities:
                raise ReviewOutputValidationError(
                    f"LLM returned a duplicate finding for {candidate.file_path}"
                )
            identities.add(identity)
            findings.append(
                ReviewFinding(
                    title=candidate.title,
                    explanation=candidate.explanation,
                    suggestion=candidate.suggestion,
                    file_path=candidate.file_path,
                    severity=candidate.severity,
                    category=candidate.category,
                    confidence=candidate.confidence,
                )
            )

        return ReviewReport(
            summary=output.summary,
            risk_level=_derive_risk_level(findings),
            findings=tuple(findings),
            model_name=self._provider.model_name,
            prompt_version=prompt.version,
            reviewed_file_count=len(context.reviewable_files),
            skipped_file_count=len(context.skipped_files),
        )


def _derive_risk_level(findings: list[ReviewFinding]) -> ReviewRiskLevel:
    if not findings:
        return ReviewRiskLevel.NONE
    return max(
        (_SEVERITY_TO_RISK[finding.severity] for finding in findings),
        key=_RISK_ORDER.__getitem__,
    )
