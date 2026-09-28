import hashlib
import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

from app.domain.review.report import ReviewFinding

FINDING_IDENTITY_VERSION = "finding-text-v3"
_MIN_TITLE_CONTAINMENT = 0.75
_MIN_COMBINED_CONTAINMENT = 0.65
_MIN_CODE_EVIDENCE_LENGTH = 12
_MIN_SHARED_EXPLANATION_SIGNALS = 4
_GENERIC_TITLE_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "by",
        "code",
        "error",
        "file",
        "from",
        "function",
        "in",
        "is",
        "issue",
        "line",
        "method",
        "of",
        "query",
        "the",
        "through",
        "to",
        "via",
        "with",
    }
)
_GENERIC_EXPLANATION_TOKENS = _GENERIC_TITLE_TOKENS | frozenset(
    {
        "after",
        "also",
        "any",
        "as",
        "at",
        "be",
        "because",
        "before",
        "being",
        "both",
        "can",
        "could",
        "does",
        "every",
        "for",
        "if",
        "instead",
        "into",
        "it",
        "its",
        "no",
        "not",
        "on",
        "or",
        "over",
        "per",
        "performs",
        "so",
        "that",
        "then",
        "this",
        "under",
        "use",
        "used",
        "using",
        "value",
        "values",
        "when",
        "without",
    }
)


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
    if (
        title_containment >= _MIN_TITLE_CONTAINMENT
        and combined_containment >= _MIN_COMBINED_CONTAINMENT
    ):
        return True

    return _shares_root_cause_and_code_evidence(left, right)


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


def _shares_root_cause_and_code_evidence(
    left: ReviewFinding,
    right: ReviewFinding,
) -> bool:
    if not (_code_evidence(left.explanation) & _code_evidence(right.explanation)):
        return False

    left_title_tokens = _signal_tokens(left.title)
    right_title_tokens = _signal_tokens(right.title)
    if (
        len(left_title_tokens & right_title_tokens) >= 2
        and _signal_bigrams(left.title) & _signal_bigrams(right.title)
    ):
        return True

    return _shares_explanation_root_cause(left.explanation, right.explanation)


def _shares_explanation_root_cause(left: str, right: str) -> bool:
    left_prose = _without_code_evidence(left)
    right_prose = _without_code_evidence(right)
    left_tokens = _explanation_signal_tokens(left_prose)
    right_tokens = _explanation_signal_tokens(right_prose)
    if len(left_tokens & right_tokens) < _MIN_SHARED_EXPLANATION_SIGNALS:
        return False
    return bool(
        _explanation_signal_bigrams(left_prose)
        & _explanation_signal_bigrams(right_prose)
    )


def _signal_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_finding_text(value).split()
        if token not in _GENERIC_TITLE_TOKENS
    }


def _signal_bigrams(value: str) -> set[tuple[str, str]]:
    tokens = [
        token
        for token in normalize_finding_text(value).split()
        if token not in _GENERIC_TITLE_TOKENS
    ]
    return set(pairwise(tokens))


def _explanation_signal_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_finding_text(value).split()
        if token not in _GENERIC_EXPLANATION_TOKENS
    }


def _explanation_signal_bigrams(value: str) -> set[tuple[str, str]]:
    tokens = [
        token
        for token in normalize_finding_text(value).split()
        if token not in _GENERIC_EXPLANATION_TOKENS
    ]
    return set(pairwise(tokens))


def _without_code_evidence(value: str) -> str:
    return re.sub(r"`[^`\r\n]+`", " ", value)


def _code_evidence(value: str) -> set[str]:
    evidence: set[str] = set()
    for raw_span in re.findall(r"`([^`\r\n]+)`", value):
        normalized = normalize_finding_text(raw_span)
        if len(normalized) >= _MIN_CODE_EVIDENCE_LENGTH:
            evidence.add(normalized)
    return evidence
