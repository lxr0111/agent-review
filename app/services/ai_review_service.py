from dataclasses import dataclass

from app.domain.review.entities import ReviewContext
from app.domain.review.exceptions import ReviewOutputValidationError
from app.domain.review.report import (
    ReviewFinding,
    ReviewReport,
    ReviewRiskLevel,
    derive_review_risk,
)
from app.providers.llm.prompt_builder import PROMPT_VERSION, ReviewPromptBuilder
from app.providers.llm.protocol import LLMProvider, LLMTokenUsage


@dataclass(frozen=True, slots=True)
class AIReviewResult:
    report: ReviewReport
    usage: LLMTokenUsage | None


class AIReviewService:
    def __init__(
        self,
        provider: LLMProvider,
        prompt_builder: ReviewPromptBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder or ReviewPromptBuilder()

    def review(self, context: ReviewContext) -> ReviewReport:
        return self.review_with_metadata(context).report

    def review_with_metadata(self, context: ReviewContext) -> AIReviewResult:
        if not context.reviewable_files:
            return AIReviewResult(
                report=ReviewReport(
                    summary="No reviewable code changes were available.",
                    risk_level=ReviewRiskLevel.NONE,
                    findings=(),
                    model_name=None,
                    prompt_version=PROMPT_VERSION,
                    reviewed_file_count=0,
                    skipped_file_count=len(context.skipped_files),
                ),
                usage=None,
            )

        prompt = self._prompt_builder.build(context)
        provider_result = self._provider.generate_review(prompt)
        output = provider_result.output
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

        return AIReviewResult(
            report=ReviewReport(
                summary=output.summary,
                risk_level=derive_review_risk(findings),
                findings=tuple(findings),
                model_name=self._provider.model_name,
                prompt_version=prompt.version,
                reviewed_file_count=len(context.reviewable_files),
                skipped_file_count=len(context.skipped_files),
            ),
            usage=provider_result.usage,
        )
