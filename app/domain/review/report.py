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
