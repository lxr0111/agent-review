from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

from app.providers.github.exceptions import (
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTransientError,
)


class GitHubHttpTransport:
    def __init__(self, client: httpx.Client, api_version: str) -> None:
        self._client = client
        self._api_version = api_version

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    def request(
        self,
        method: str,
        url: str,
        *,
        bearer_token: str,
        expected_status: int,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "AgentReview/0.1.0",
            "X-GitHub-Api-Version": self._api_version,
        }
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise GitHubTransientError("GitHub API request timed out") from exc
        except httpx.TransportError as exc:
            raise GitHubTransientError("GitHub API connection failed") from exc

        if response.status_code != expected_status:
            _raise_for_status(response)
            raise GitHubResponseError(
                "GitHub API returned an unexpected success status",
                status_code=response.status_code,
                request_id=response.headers.get("x-github-request-id"),
            )
        return response


def _raise_for_status(response: httpx.Response) -> None:
    status_code = response.status_code
    request_id = response.headers.get("x-github-request-id")
    error_details = {"status_code": status_code, "request_id": request_id}

    if status_code == 401:
        raise GitHubAuthenticationError("GitHub authentication failed", **error_details)
    if status_code == 404:
        raise GitHubNotFoundError("GitHub resource was not found", **error_details)
    if status_code == 429 or _is_rate_limited(response):
        raise GitHubRateLimitError(
            "GitHub API rate limit was exceeded",
            status_code=status_code,
            request_id=request_id,
            retry_after_seconds=_parse_non_negative_int(response.headers.get("retry-after")),
            rate_limit_reset_at=_parse_epoch(response.headers.get("x-ratelimit-reset")),
        )
    if status_code == 403:
        raise GitHubPermissionError("GitHub App permission was denied", **error_details)
    if 500 <= status_code:
        raise GitHubTransientError("GitHub API is temporarily unavailable", **error_details)
    if 400 <= status_code:
        raise GitHubResponseError("GitHub API request was rejected", **error_details)


def _is_rate_limited(response: httpx.Response) -> bool:
    return response.status_code == 403 and (
        response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
    )


def _parse_non_negative_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_epoch(value: str | None) -> datetime | None:
    epoch = _parse_non_negative_int(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
