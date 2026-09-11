import random
import re
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import OperationalError

from app.providers.github.exceptions import GitHubProviderError, GitHubRateLimitError
from app.providers.llm.exceptions import LLMProviderError, LLMRateLimitError


def is_retryable_review_error(error: Exception) -> bool:
    if isinstance(error, (GitHubProviderError, LLMProviderError)):
        return error.retryable
    return isinstance(error, (ConnectionError, TimeoutError, OperationalError))


def retry_countdown_seconds(
    error: Exception,
    *,
    retries: int,
    base_seconds: int,
    max_seconds: int,
    jitter: Callable[[], float] = random.random,
    now: Callable[[], datetime] | None = None,
) -> int:
    if retries < 0 or base_seconds < 1 or max_seconds < base_seconds:
        raise ValueError("Invalid review retry policy configuration")
    exponential = min(max_seconds, base_seconds * (2**retries))
    delay = exponential + int(exponential * 0.25 * jitter())
    provider_delay = _provider_retry_after(error, now=now)
    return int(max(delay, provider_delay))


def review_error_code(error: Exception) -> str:
    name = type(error).__name__.replace("GitHub", "Github").replace("LLM", "Llm")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()[:100]


def _provider_retry_after(
    error: Exception,
    *,
    now: Callable[[], datetime] | None,
) -> int:
    if isinstance(error, GitHubRateLimitError) and error.retry_after_seconds is not None:
        return error.retry_after_seconds
    if isinstance(error, LLMRateLimitError) and error.retry_after_seconds is not None:
        return error.retry_after_seconds
    if isinstance(error, GitHubRateLimitError) and error.rate_limit_reset_at is not None:
        current = (now or (lambda: datetime.now(UTC)))()
        if current.tzinfo is None:
            raise ValueError("Retry policy clock must be timezone-aware")
        return max(0, int((error.rate_limit_reset_at - current.astimezone(UTC)).total_seconds()))
    return 0
