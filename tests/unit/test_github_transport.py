from datetime import UTC, datetime

import httpx
import pytest

from app.providers.github.exceptions import (
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTransientError,
)
from app.providers.github.transport import GitHubHttpTransport


def _request_with_response(status_code: int, headers=None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, request=request)

    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    GitHubHttpTransport(client, "2026-03-10").request(
        "GET",
        "/resource",
        bearer_token="ghs_must-not-leak",
        expected_status=200,
    )


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, GitHubAuthenticationError),
        (403, GitHubPermissionError),
        (404, GitHubNotFoundError),
        (422, GitHubResponseError),
        (500, GitHubTransientError),
        (503, GitHubTransientError),
    ],
)
def test_transport_classifies_http_errors(
    status_code: int, expected_error: type[Exception]
) -> None:
    with pytest.raises(expected_error) as caught:
        _request_with_response(status_code, {"X-GitHub-Request-Id": "request-123"})

    assert "ghs_must-not-leak" not in str(caught.value)


def test_transport_preserves_rate_limit_retry_information() -> None:
    reset_epoch = 1_788_499_200

    with pytest.raises(GitHubRateLimitError) as caught:
        _request_with_response(
            403,
            {
                "Retry-After": "30",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_epoch),
                "X-GitHub-Request-Id": "request-123",
            },
        )

    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 30
    assert caught.value.rate_limit_reset_at == datetime.fromtimestamp(reset_epoch, tz=UTC)
    assert caught.value.request_id == "request-123"


def test_transport_converts_timeout_to_retryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret transport details", request=request)

    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    transport = GitHubHttpTransport(client, "2026-03-10")

    with pytest.raises(GitHubTransientError) as caught:
        transport.request(
            "GET",
            "/resource",
            bearer_token="ghs_must-not-leak",
            expected_status=200,
        )

    assert caught.value.retryable is True
    assert str(caught.value) == "GitHub API request timed out"
