from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

from app.domain.review.entities import ReviewContext
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewReport,
    derive_review_risk,
)
from app.services.review_agent import AgentReviewResult, AgentRole
from app.services.review_finding_identity import (
    FINDING_IDENTITY_VERSION,
    FindingMatchKind,
    finding_identity,
    finding_match_kind,
    findings_describe_same_issue,
    normalize_finding_text,
)
from app.services.review_finding_validator import (
    ReviewFindingValidator,
    ValidatedAgentFinding,
)

AGGREGATOR_PROMPT_VERSION = "multi-agent-v2"


@runtime_checkable
class ReviewAggregator(Protocol):
    def aggregate(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewReport: ...


class PassthroughReviewAggregator:
    """Returns the only agent report without changing production output."""

    def aggregate(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewReport:
        del context
        if len(results) != 1:
            raise ValueError("Passthrough review aggregation requires exactly one result")
        return results[0].report


class AggregationAction(StrEnum):
    KEPT = "kept"
    MERGED = "merged"


@dataclass(frozen=True, slots=True)
class AggregationDecision:
    action: AggregationAction
    source_role: AgentRole
    file_path: str
    title: str
    canonical_fingerprint: str
    reason: str


@dataclass(frozen=True, slots=True)
class AggregationConflict:
    canonical_fingerprint: str
    field: str
    selected_value: str
    observed_values: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ReviewAggregationResult:
    report: ReviewReport
    finding_identity_version: str
    decisions: tuple[AggregationDecision, ...]
    conflicts: tuple[AggregationConflict, ...]
    source_roles: tuple[AgentRole, ...]


class DeterministicReviewAggregator:
    """Validates and deterministically combines findings from multiple agents."""

    _ROLE_ORDER: ClassVar[dict[AgentRole, int]] = {
        AgentRole.CORRECTNESS: 0,
        AgentRole.SECURITY: 1,
        AgentRole.PERFORMANCE: 2,
        AgentRole.MAINTAINABILITY: 3,
        AgentRole.GENERAL: 4,
    }
    _ROLE_CATEGORIES: ClassVar[dict[AgentRole, frozenset[FindingCategory]]] = {
        AgentRole.CORRECTNESS: frozenset(
            {
                FindingCategory.CORRECTNESS,
                FindingCategory.RELIABILITY,
            }
        ),
        AgentRole.SECURITY: frozenset({FindingCategory.SECURITY}),
        AgentRole.PERFORMANCE: frozenset({FindingCategory.PERFORMANCE}),
        AgentRole.MAINTAINABILITY: frozenset({FindingCategory.MAINTAINABILITY}),
        AgentRole.GENERAL: frozenset(),
    }
    _SEVERITY_ORDER: ClassVar[dict[FindingSeverity, int]] = {
        FindingSeverity.CRITICAL: 0,
        FindingSeverity.HIGH: 1,
        FindingSeverity.MEDIUM: 2,
        FindingSeverity.LOW: 3,
    }

    def __init__(self, validator: ReviewFindingValidator | None = None) -> None:
        self._validator = validator or ReviewFindingValidator()

    def aggregate(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewReport:
        return self.aggregate_with_metadata(context=context, results=results).report

    def aggregate_with_metadata(
        self,
        *,
        context: ReviewContext,
        results: tuple[AgentReviewResult, ...],
    ) -> ReviewAggregationResult:
        if not results:
            raise ValueError("Deterministic review aggregation requires at least one result")
        roles = tuple(result.role for result in results)
        if len(set(roles)) != len(roles):
            raise ValueError("Deterministic review aggregation requires unique agent roles")

        candidates = tuple(
            candidate
            for result in results
            for candidate in self._validator.validate(context=context, result=result)
        )
        groups = self._group_candidates(candidates)
        selected: list[ReviewFinding] = []
        decisions: list[AggregationDecision] = []
        conflicts: list[AggregationConflict] = []

        for group in groups:
            evidence_constrained = self._requires_evidence_constrained_ranking(group)
            canonical = min(
                group,
                key=lambda candidate: self._canonical_rank(
                    candidate,
                    evidence_constrained=evidence_constrained,
                ),
            )
            canonical_identity = finding_identity(canonical.finding)
            selected.append(canonical.finding)
            selection_reason = (
                "selected by confidence before role alignment because duplicate identity "
                "relied on shared problem and remediation code across conflicting categories"
                if evidence_constrained
                else "selected by role alignment, confidence, and deterministic tie-breaks"
            )
            decisions.append(
                AggregationDecision(
                    action=AggregationAction.KEPT,
                    source_role=canonical.role,
                    file_path=canonical.finding.file_path,
                    title=canonical.finding.title,
                    canonical_fingerprint=canonical_identity.fingerprint,
                    reason=selection_reason,
                )
            )
            for candidate in group:
                if candidate is canonical:
                    continue
                decisions.append(
                    AggregationDecision(
                        action=AggregationAction.MERGED,
                        source_role=candidate.role,
                        file_path=candidate.finding.file_path,
                        title=candidate.finding.title,
                        canonical_fingerprint=canonical_identity.fingerprint,
                        reason=(
                            "merged as the same issue using shared problem and remediation "
                            "code evidence"
                            if evidence_constrained
                            else "merged as the same issue using file and text identity"
                        ),
                    )
                )
            conflicts.extend(self._conflicts(group, canonical))

        findings = tuple(sorted(selected, key=self._finding_order))
        source_roles = tuple(sorted(set(roles), key=self._ROLE_ORDER.__getitem__))
        report = ReviewReport(
            summary=self._summary(findings),
            risk_level=derive_review_risk(findings),
            findings=findings,
            model_name=self._model_name(results),
            prompt_version=AGGREGATOR_PROMPT_VERSION,
            reviewed_file_count=len(context.reviewable_files),
            skipped_file_count=len(context.skipped_files),
        )
        return ReviewAggregationResult(
            report=report,
            finding_identity_version=FINDING_IDENTITY_VERSION,
            decisions=tuple(decisions),
            conflicts=tuple(conflicts),
            source_roles=source_roles,
        )

    def _group_candidates(
        self,
        candidates: tuple[ValidatedAgentFinding, ...],
    ) -> tuple[tuple[ValidatedAgentFinding, ...], ...]:
        groups: list[list[ValidatedAgentFinding]] = []
        for candidate in sorted(candidates, key=self._candidate_order):
            matching_group = next(
                (
                    group
                    for group in groups
                    if all(
                        findings_describe_same_issue(candidate.finding, member.finding)
                        for member in group
                    )
                ),
                None,
            )
            if matching_group is None:
                groups.append([candidate])
            else:
                matching_group.append(candidate)
        return tuple(tuple(group) for group in groups)

    def _canonical_rank(
        self,
        candidate: ValidatedAgentFinding,
        *,
        evidence_constrained: bool,
    ) -> tuple[float, float, int, int, str, str]:
        finding = candidate.finding
        role_aligned = finding.category in self._ROLE_CATEGORIES[candidate.role]
        content_length = len(finding.explanation.strip()) + len(finding.suggestion.strip())
        primary_rank: float = -float(role_aligned)
        confidence_rank: float = -finding.confidence
        if evidence_constrained:
            primary_rank, confidence_rank = confidence_rank, primary_rank
        return (
            primary_rank,
            confidence_rank,
            -content_length,
            self._ROLE_ORDER[candidate.role],
            normalize_finding_text(finding.title),
            normalize_finding_text(finding.explanation),
        )

    @staticmethod
    def _requires_evidence_constrained_ranking(
        group: tuple[ValidatedAgentFinding, ...],
    ) -> bool:
        categories = {candidate.finding.category for candidate in group}
        if len(categories) < 2:
            return False
        return any(
            finding_match_kind(left.finding, right.finding)
            == FindingMatchKind.PROBLEM_AND_REMEDIATION_CODE
            for position, left in enumerate(group)
            for right in group[position + 1 :]
        )

    def _candidate_order(
        self,
        candidate: ValidatedAgentFinding,
    ) -> tuple[str, str, str, int]:
        identity = finding_identity(candidate.finding)
        return (
            identity.file_path,
            identity.normalized_title,
            identity.normalized_explanation,
            self._ROLE_ORDER[candidate.role],
        )

    def _finding_order(self, finding: ReviewFinding) -> tuple[int, str, str]:
        return (
            self._SEVERITY_ORDER[finding.severity],
            finding.file_path,
            normalize_finding_text(finding.title),
        )

    @staticmethod
    def _summary(findings: tuple[ReviewFinding, ...]) -> str:
        if not findings:
            return "Multi-agent review found no actionable findings."
        file_count = len({finding.file_path for finding in findings})
        finding_label = "finding" if len(findings) == 1 else "findings"
        file_label = "file" if file_count == 1 else "files"
        return (
            f"Multi-agent review found {len(findings)} actionable {finding_label} "
            f"across {file_count} {file_label}."
        )

    @staticmethod
    def _model_name(results: tuple[AgentReviewResult, ...]) -> str | None:
        model_names = sorted(
            {
                result.report.model_name
                for result in results
                if result.report.model_name is not None
            }
        )
        if not model_names:
            return None
        if len(model_names) == 1:
            return model_names[0]
        return "multi-agent"

    @staticmethod
    def _conflicts(
        group: tuple[ValidatedAgentFinding, ...],
        canonical: ValidatedAgentFinding,
    ) -> tuple[AggregationConflict, ...]:
        fingerprint = finding_identity(canonical.finding).fingerprint
        conflicts: list[AggregationConflict] = []
        for field in ("category", "severity"):
            selected_value = getattr(canonical.finding, field).value
            observed_values = tuple(
                sorted({getattr(candidate.finding, field).value for candidate in group})
            )
            if len(observed_values) > 1:
                conflicts.append(
                    AggregationConflict(
                        canonical_fingerprint=fingerprint,
                        field=field,
                        selected_value=selected_value,
                        observed_values=observed_values,
                        reason=(
                            "kept the label from the canonical finding selected by the "
                            "group's deterministic evidence-aware ranking"
                        ),
                    )
                )
        return tuple(conflicts)
