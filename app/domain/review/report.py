from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class FindingSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    SECURITY = "security"
    CORRECTNESS = "correctness"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"


class ReviewRiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    title: str
    explanation: str
    suggestion: str
    file_path: str
    severity: FindingSeverity
    category: FindingCategory
    confidence: float


@dataclass(frozen=True, slots=True)
class ReviewReport:
    summary: str
    risk_level: ReviewRiskLevel
    findings: tuple[ReviewFinding, ...]
    model_name: str | None
    prompt_version: str
    reviewed_file_count: int
    skipped_file_count: int


def derive_review_risk(findings: Iterable[ReviewFinding]) -> ReviewRiskLevel:
    severity_to_risk = {
        FindingSeverity.LOW: ReviewRiskLevel.LOW,
        FindingSeverity.MEDIUM: ReviewRiskLevel.MEDIUM,
        FindingSeverity.HIGH: ReviewRiskLevel.HIGH,
        FindingSeverity.CRITICAL: ReviewRiskLevel.CRITICAL,
    }
    risk_order = {
        ReviewRiskLevel.NONE: 0,
        ReviewRiskLevel.LOW: 1,
        ReviewRiskLevel.MEDIUM: 2,
        ReviewRiskLevel.HIGH: 3,
        ReviewRiskLevel.CRITICAL: 4,
    }
    return max(
        (severity_to_risk[finding.severity] for finding in findings),
        key=risk_order.__getitem__,
        default=ReviewRiskLevel.NONE,
    )
