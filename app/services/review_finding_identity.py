import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.domain.review.report import ReviewFinding

_MIN_TITLE_CONTAINMENT = 0.75
_MIN_COMBINED_CONTAINMENT = 0.65


@dataclass(frozen=True, slots=True)
class FindingIdentity:
    file_path: str
    normalized_title: str
    normalized_explanation: str
    fingerprint: str


def finding_identity(finding: ReviewFinding) -> FindingIdentity:
    normalized_title = normalize_finding_text(finding.title)
    normalized_explanation = normalize_finding_text(finding.explanation)
    payload = f"{finding.file_path}\x1f{normalized_title}\x1f{normalized_explanation}"
    return FindingIdentity(
        file_path=finding.file_path,
        normalized_title=normalized_title,
        normalized_explanation=normalized_explanation,
        fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def findings_describe_same_issue(left: ReviewFinding, right: ReviewFinding) -> bool:
    """Conservatively detects duplicate findings without using labels or severity."""

    if left.file_path != right.file_path:
        return False

    left_identity = finding_identity(left)
    right_identity = finding_identity(right)
    if left_identity.fingerprint == right_identity.fingerprint:
        return True

    title_containment = _token_containment(
        left_identity.normalized_title,
        right_identity.normalized_title,
    )
    combined_containment = _token_containment(
        f"{left_identity.normalized_title} {left_identity.normalized_explanation}",
        f"{right_identity.normalized_title} {right_identity.normalized_explanation}",
    )
    return (
        title_containment >= _MIN_TITLE_CONTAINMENT
        and combined_containment >= _MIN_COMBINED_CONTAINMENT
    )


def normalize_finding_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("_", " ")
    return " ".join(re.sub(r"[^\w]+", " ", normalized).split())


def _token_containment(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    smallest_size = min(len(left_tokens), len(right_tokens))
    if smallest_size == 0:
        return 0.0
    return len(left_tokens & right_tokens) / smallest_size
