from datetime import UTC, datetime, timedelta

from app.providers.github.exceptions import GitHubPermissionError, GitHubRateLimitError
from app.providers.llm.exceptions import LLMRateLimitError, LLMTransientError
from app.workers.retry_policy import (
    is_retryable_review_error,
    retry_countdown_seconds,
    review_error_code,
)

NOW = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)


def test_retryable_provider_errors_are_distinguished_from_permanent_errors() -> None:
    assert is_retryable_review_error(LLMTransientError("temporary"))
    assert not is_retryable_review_error(GitHubPermissionError("denied", status_code=403))
    assert not is_retryable_review_error(ValueError("invalid output"))


def test_retry_countdown_uses_exponential_backoff_and_jitter() -> None:
    delay = retry_countdown_seconds(
        LLMTransientError("temporary"),
        retries=2,
        base_seconds=5,
        max_seconds=300,
        jitter=lambda: 0.8,
    )

    assert delay == 24


def test_retry_countdown_honors_retry_after_header() -> None:
    error = LLMRateLimitError(
        "rate limited",
        status_code=429,
        request_id=None,
        retry_after_seconds=90,
    )

    delay = retry_countdown_seconds(
        error,
        retries=0,
        base_seconds=5,
        max_seconds=300,
        jitter=lambda: 0,
    )

    assert delay == 90


def test_retry_countdown_uses_github_rate_limit_reset() -> None:
    error = GitHubRateLimitError(
        "rate limited",
        status_code=403,
        request_id=None,
        retry_after_seconds=None,
        rate_limit_reset_at=NOW + timedelta(seconds=120),
    )

    delay = retry_countdown_seconds(
        error,
        retries=0,
        base_seconds=5,
        max_seconds=300,
        jitter=lambda: 0,
        now=lambda: NOW,
    )

    assert delay == 120


def test_error_code_preserves_provider_acronyms() -> None:
    assert review_error_code(LLMTransientError("temporary")) == "llm_transient_error"
