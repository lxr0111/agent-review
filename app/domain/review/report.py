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
class ReviewLineRange:
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError("Review line range must start at a positive line")
        if self.end_line < self.start_line:
            raise ValueError("Review line range end must not precede its start")


def optional_review_line_range(
    start_line: int | None,
    end_line: int | None,
) -> ReviewLineRange | None:
    if start_line is None and end_line is None:
        return None
    if start_line is None or end_line is None:
        raise ValueError("Review line range must contain both start and end lines")
    return ReviewLineRange(start_line=start_line, end_line=end_line)


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    title: str
    explanation: str
    suggestion: str
    file_path: str
    severity: FindingSeverity
    category: FindingCategory
    confidence: float
    location: ReviewLineRange | None = None


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
