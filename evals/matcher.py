import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.review.report import ReviewFinding
from evals.schemas import EvalExpectedFinding

MIN_MATCH_SCORE = 0.5
MIN_TERM_COVERAGE = 0.5


class MatcherVersion(StrEnum):
    TEXT_V1 = "text-v1"
    TEXT_V2 = "text-v2"


LEGACY_MATCHER_VERSION = MatcherVersion.TEXT_V1
CURRENT_MATCHER_VERSION = MatcherVersion.TEXT_V2


class MatchableFinding(Protocol):
    title: str
    explanation: str
    suggestion: str
    file_path: str


@dataclass(frozen=True, slots=True)
class FindingMatch:
    expected_index: int
    actual_index: int
    score: float


@dataclass(frozen=True, slots=True)
class FindingMatchResult:
    matches: tuple[FindingMatch, ...]
    unmatched_expected_indices: tuple[int, ...]
    unmatched_actual_indices: tuple[int, ...]


def match_findings(
    expected: Sequence[EvalExpectedFinding],
    actual: Sequence[ReviewFinding | MatchableFinding],
    *,
    matcher_version: MatcherVersion = CURRENT_MATCHER_VERSION,
) -> FindingMatchResult:
    candidates: list[FindingMatch] = []
    for expected_index, expected_finding in enumerate(expected):
        for actual_index, actual_finding in enumerate(actual):
            if expected_finding.file_path != actual_finding.file_path:
                continue
            score, term_coverage = _text_match_score(
                expected_finding,
                actual_finding,
                matcher_version=matcher_version,
            )
            if score >= MIN_MATCH_SCORE and term_coverage >= MIN_TERM_COVERAGE:
                candidates.append(
                    FindingMatch(
                        expected_index=expected_index,
                        actual_index=actual_index,
                        score=score,
                    )
                )

    matched_expected: set[int] = set()
    matched_actual: set[int] = set()
    matches: list[FindingMatch] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item.score, item.expected_index, item.actual_index),
    ):
        if candidate.expected_index in matched_expected:
            continue
        if candidate.actual_index in matched_actual:
            continue
        matched_expected.add(candidate.expected_index)
        matched_actual.add(candidate.actual_index)
        matches.append(candidate)

    return FindingMatchResult(
        matches=tuple(sorted(matches, key=lambda item: item.expected_index)),
        unmatched_expected_indices=tuple(
            index for index in range(len(expected)) if index not in matched_expected
        ),
        unmatched_actual_indices=tuple(
            index for index in range(len(actual)) if index not in matched_actual
        ),
    )


def _text_match_score(
    expected: EvalExpectedFinding,
    actual: ReviewFinding | MatchableFinding,
    *,
    matcher_version: MatcherVersion,
) -> tuple[float, float]:
    combined_actual_text = f"{actual.title} {actual.explanation} {actual.suggestion}"
    normalizer = (
        _normalize_text_v1 if matcher_version == MatcherVersion.TEXT_V1 else _normalize_text_v2
    )
    term_matcher = (
        _contains_phrase_v1 if matcher_version == MatcherVersion.TEXT_V1 else _contains_terms_v2
    )
    actual_text = normalizer(combined_actual_text)
    matched_groups = sum(
        any(term_matcher(actual_text, alternative) for alternative in alternatives)
        for alternatives in expected.match_terms
    )
    term_coverage = matched_groups / len(expected.match_terms)

    expected_tokens = set(normalizer(expected.description).split())
    actual_tokens = set(actual_text.split())
    description_overlap = (
        len(expected_tokens & actual_tokens) / len(expected_tokens) if expected_tokens else 0.0
    )
    score = (0.8 * term_coverage) + (0.2 * description_overlap)
    return min(score, 1.0), term_coverage


def _contains_phrase_v1(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text_v1(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "


def _contains_terms_v2(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text_v2(phrase)
    if not normalized_phrase:
        return False
    if f" {normalized_phrase} " in f" {normalized_text} ":
        return True
    phrase_tokens = set(normalized_phrase.split())
    return phrase_tokens.issubset(set(normalized_text.split()))


def _normalize_text_v1(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized).split())


def _normalize_text_v2(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    operator_words = (
        ("<=", " less than or equal "),
        (">=", " greater than or equal "),
        ("==", " equal "),
        ("!=", " not equal "),
    )
    for operator, words in operator_words:
        normalized = normalized.replace(operator, words)
    return " ".join(re.sub(r"[^\w]+", " ", normalized).split())
