from collections.abc import Mapping
from datetime import timedelta
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from app.core.config import Settings
from app.domain.review.entities import ChangedFile, PullRequestSnapshot
from app.domain.review.enums import FileChangeStatus
from app.providers.github.auth import GitHubAppAuth
from app.providers.github.exceptions import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubFileLimitExceeded,
    GitHubPayloadError,
)
from app.providers.github.schemas import (
    GitHubChangedFileResponse,
    GitHubPullRequestResponse,
)
from app.providers.github.transport import GitHubHttpTransport

_CHANGED_FILES_ADAPTER = TypeAdapter(list[GitHubChangedFileResponse])


class GitHubApiClient:
    def __init__(
        self,
        *,
        auth: GitHubAppAuth,
        transport: GitHubHttpTransport,
        max_files: int = 3_000,
        owned_http_client: httpx.Client | None = None,
    ) -> None:
        if not 1 <= max_files <= 3_000:
            raise GitHubConfigurationError("GitHub PR file limit must be between 1 and 3000")
        self._auth = auth
        self._transport = transport
        self._max_files = max_files
        self._owned_http_client = owned_http_client

    @classmethod
    def from_settings(cls, settings: Settings) -> "GitHubApiClient":
        if settings.github_app_client_id is None:
            raise GitHubConfigurationError("GITHUB_APP_CLIENT_ID is not configured")
        if settings.github_private_key_path is None:
            raise GitHubConfigurationError("GITHUB_PRIVATE_KEY_PATH is not configured")

        http_client = httpx.Client(
            base_url=settings.github_api_url,
            timeout=settings.github_http_timeout_seconds,
            follow_redirects=True,
        )
        transport = GitHubHttpTransport(http_client, settings.github_api_version)
        try:
            auth = GitHubAppAuth.from_private_key_file(
                client_id=settings.github_app_client_id,
                private_key_path=settings.github_private_key_path,
                transport=transport,
                refresh_buffer=timedelta(seconds=settings.github_token_refresh_buffer_seconds),
            )
        except Exception:
            http_client.close()
            raise
        return cls(
            auth=auth,
            transport=transport,
            max_files=settings.github_max_pull_request_files,
            owned_http_client=http_client,
        )

    def get_pull_request(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> PullRequestSnapshot:
        repository_path = _repository_path(repository)
        _validate_pull_request_number(pull_request_number)
        response = self._authenticated_request(
            "GET",
            f"/repos/{repository_path}/pulls/{pull_request_number}",
            installation_id=installation_id,
            expected_status=200,
        )
        try:
            payload = GitHubPullRequestResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise _payload_error("GitHub pull request response is invalid", response) from exc
        if payload.number != pull_request_number:
            raise _payload_error("GitHub returned a different pull request number", response)

        return PullRequestSnapshot(
            repository=repository,
            number=payload.number,
            title=payload.title,
            body=payload.body,
            base_sha=payload.base.sha,
            head_sha=payload.head.sha,
            draft=payload.draft,
            author_login=payload.user.login if payload.user is not None else None,
        )

    def list_pull_request_files(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[ChangedFile, ...]:
        repository_path = _repository_path(repository)
        _validate_pull_request_number(pull_request_number)
        url = f"/repos/{repository_path}/pulls/{pull_request_number}/files"
        params: Mapping[str, str | int] | None = {"per_page": 100, "page": 1}
        visited_urls: set[str] = set()
        changed_files: list[ChangedFile] = []

        while True:
            response = self._authenticated_request(
                "GET",
                url,
                installation_id=installation_id,
                expected_status=200,
                params=params,
            )
            page = self._parse_changed_files(response)
            if len(changed_files) + len(page) > self._max_files:
                raise GitHubFileLimitExceeded(
                    f"Pull request exceeds the configured {self._max_files} file limit"
                )
            changed_files.extend(page)

            next_url = self._get_next_page_url(response)
            if next_url is None:
                return tuple(changed_files)
            if len(changed_files) >= self._max_files:
                raise GitHubFileLimitExceeded(
                    f"Pull request exceeds the configured {self._max_files} file limit"
                )
            if next_url in visited_urls:
                raise _payload_error("GitHub pagination contains a loop", response)
            visited_urls.add(next_url)
            url = next_url
            params = None

    def close(self) -> None:
        if self._owned_http_client is not None:
            self._owned_http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _authenticated_request(
        self,
        method: str,
        url: str,
        *,
        installation_id: int,
        expected_status: int,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        token = self._auth.get_installation_token(installation_id)
        try:
            return self._transport.request(
                method,
                url,
                bearer_token=token.value.get_secret_value(),
                expected_status=expected_status,
                params=params,
            )
        except GitHubAuthenticationError:
            self._auth.invalidate(installation_id)
            refreshed = self._auth.get_installation_token(installation_id)
            return self._transport.request(
                method,
                url,
                bearer_token=refreshed.value.get_secret_value(),
                expected_status=expected_status,
                params=params,
            )

    def _parse_changed_files(self, response: httpx.Response) -> tuple[ChangedFile, ...]:
        try:
            payloads = _CHANGED_FILES_ADAPTER.validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise _payload_error("GitHub pull request files response is invalid", response) from exc

        changed_files: list[ChangedFile] = []
        for payload in payloads:
            try:
                status = FileChangeStatus(payload.status)
            except ValueError as exc:
                raise _payload_error(
                    f"GitHub returned an unsupported file status: {payload.status}",
                    response,
                ) from exc
            changed_files.append(
                ChangedFile(
                    path=payload.filename,
                    status=status,
                    additions=payload.additions,
                    deletions=payload.deletions,
                    changes=payload.changes,
                    patch=payload.patch,
                    previous_path=payload.previous_filename,
                )
            )
        return tuple(changed_files)

    def _get_next_page_url(self, response: httpx.Response) -> str | None:
        next_link = response.links.get("next")
        if next_link is None:
            return None
        next_url = next_link.get("url")
        if not isinstance(next_url, str) or not next_url:
            raise _payload_error("GitHub pagination link is invalid", response)

        parsed_next = httpx.URL(next_url)
        base_url = self._transport.base_url
        if (
            parsed_next.scheme != base_url.scheme
            or parsed_next.host != base_url.host
            or parsed_next.port != base_url.port
        ):
            raise _payload_error("GitHub pagination link points to an unexpected origin", response)
        return str(parsed_next)


def _repository_path(repository: str) -> str:
    parts = repository.split("/")
    if len(parts) != 2:
        raise GitHubPayloadError("GitHub repository must use the owner/name format")
    owner, name = parts
    if not owner or not name or len(owner) > 100 or len(name) > 100:
        raise GitHubPayloadError("GitHub repository owner or name is invalid")
    if any(character.isspace() or ord(character) < 32 for character in owner + name):
        raise GitHubPayloadError("GitHub repository owner or name is invalid")
    if owner in {".", ".."} or name in {".", ".."}:
        raise GitHubPayloadError("GitHub repository owner or name is invalid")
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


def _validate_pull_request_number(pull_request_number: int) -> None:
    if pull_request_number <= 0:
        raise GitHubPayloadError("GitHub pull request number must be positive")


def _payload_error(message: str, response: httpx.Response) -> GitHubPayloadError:
    return GitHubPayloadError(
        message,
        status_code=response.status_code,
        request_id=response.headers.get("x-github-request-id"),
    )
