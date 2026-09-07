import json
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from app.domain.review.enums import FileChangeStatus
from app.providers.github import GitHubApiClient, GitHubProvider
from app.providers.github.auth import GitHubAppAuth, InstallationToken
from app.providers.github.exceptions import (
    GitHubConfigurationError,
    GitHubFileLimitExceeded,
    GitHubPayloadError,
)
from app.providers.github.transport import GitHubHttpTransport

TOKEN_EXPIRY = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def _pull_request_response(number: int = 7) -> dict[str, object]:
    return {
        "number": number,
        "title": "Add GitHub provider",
        "body": "Fetch pull request metadata and files.",
        "draft": False,
        "user": {"login": "octocat"},
        "base": {"sha": "123456abcdef"},
        "head": {"sha": "abcdef123456"},
    }


def _changed_file(
    filename: str,
    *,
    status: str = "modified",
    patch: str | None = "@@ -1 +1 @@\n-old\n+new",
) -> dict[str, object]:
    return {
        "filename": filename,
        "status": status,
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "patch": patch,
    }


def _review_response(
    review_id: int = 9001,
    *,
    body: str = "Automated review",
    commit_id: str = "abcdef123456",
) -> dict[str, object]:
    return {
        "id": review_id,
        "body": body,
        "state": "COMMENTED",
        "html_url": f"https://github.test/reviews/{review_id}",
        "commit_id": commit_id,
    }


def _client(handler, *, max_files: int = 3_000) -> tuple[GitHubApiClient, Mock]:
    auth = Mock(spec=GitHubAppAuth)
    auth.get_installation_token.return_value = InstallationToken(
        value=SecretStr("ghs_installation-token"),
        expires_at=TOKEN_EXPIRY,
    )
    http_client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    transport = GitHubHttpTransport(http_client, "2026-03-10")
    return GitHubApiClient(auth=auth, transport=transport, max_files=max_files), auth


def test_get_pull_request_returns_internal_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/owner/repository/pulls/7"
        assert request.headers["authorization"] == "Bearer ghs_installation-token"
        return httpx.Response(200, json=_pull_request_response(), request=request)

    client, auth = _client(handler)

    result = client.get_pull_request(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
    )

    assert isinstance(client, GitHubProvider)
    assert result.repository == "owner/repository"
    assert result.number == 7
    assert result.base_sha == "123456abcdef"
    assert result.head_sha == "abcdef123456"
    assert result.author_login == "octocat"
    auth.get_installation_token.assert_called_once_with(42)


def test_get_pull_request_allows_deleted_github_user() -> None:
    payload = _pull_request_response()
    payload["user"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    client, _ = _client(handler)
    result = client.get_pull_request(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
    )

    assert result.author_login is None


def test_list_pull_request_files_follows_same_origin_pagination() -> None:
    requested_pages: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.params.get("page"))
        if request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json=[_changed_file("src/first.py")],
                headers={
                    "Link": (
                        "<https://api.github.test/repos/owner/repository/pulls/7/files"
                        '?per_page=100&page=2>; rel="next"'
                    )
                },
                request=request,
            )
        return httpx.Response(
            200,
            json=[
                {
                    **_changed_file("src/new_name.py", status="renamed", patch=None),
                    "previous_filename": "src/old_name.py",
                }
            ],
            request=request,
        )

    client, _ = _client(handler)
    files = client.list_pull_request_files(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
    )

    assert requested_pages == ["1", "2"]
    assert tuple(file.path for file in files) == ("src/first.py", "src/new_name.py")
    assert files[1].status == FileChangeStatus.RENAMED
    assert files[1].previous_path == "src/old_name.py"
    assert files[1].patch is None


def test_client_refreshes_cached_token_once_after_unauthorized_response() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            assert request.headers["authorization"] == "Bearer ghs_expired"
            return httpx.Response(401, request=request)
        assert request.headers["authorization"] == "Bearer ghs_refreshed"
        return httpx.Response(200, json=_pull_request_response(), request=request)

    client, auth = _client(handler)
    auth.get_installation_token.side_effect = [
        InstallationToken(SecretStr("ghs_expired"), TOKEN_EXPIRY),
        InstallationToken(SecretStr("ghs_refreshed"), TOKEN_EXPIRY),
    ]

    client.get_pull_request(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
    )

    auth.invalidate.assert_called_once_with(42)
    assert auth.get_installation_token.call_count == 2


@pytest.mark.parametrize(
    "repository",
    ["missing-owner", "too/many/parts", "/repository", "owner/", "../repository"],
)
def test_client_rejects_invalid_repository_before_authentication(repository: str) -> None:
    client, auth = _client(lambda request: httpx.Response(500, request=request))

    with pytest.raises(GitHubPayloadError, match="repository"):
        client.get_pull_request(
            installation_id=42,
            repository=repository,
            pull_request_number=7,
        )

    auth.get_installation_token.assert_not_called()


def test_client_rejects_cross_origin_pagination_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_changed_file("src/service.py")],
            headers={"Link": '<https://attacker.example/steal?page=2>; rel="next"'},
            request=request,
        )

    client, _ = _client(handler)

    with pytest.raises(GitHubPayloadError, match="unexpected origin"):
        client.list_pull_request_files(
            installation_id=42,
            repository="owner/repository",
            pull_request_number=7,
        )


def test_client_rejects_unknown_file_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_changed_file("src/service.py", status="future-status")],
            request=request,
        )

    client, _ = _client(handler)

    with pytest.raises(GitHubPayloadError, match="unsupported file status"):
        client.list_pull_request_files(
            installation_id=42,
            repository="owner/repository",
            pull_request_number=7,
        )


def test_client_stops_instead_of_returning_partial_file_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_changed_file("first.py"), _changed_file("second.py")],
            request=request,
        )

    client, _ = _client(handler, max_files=1)

    with pytest.raises(GitHubFileLimitExceeded, match="configured 1 file limit"):
        client.list_pull_request_files(
            installation_id=42,
            repository="owner/repository",
            pull_request_number=7,
        )


def test_client_rejects_mismatched_pull_request_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pull_request_response(number=8), request=request)

    client, _ = _client(handler)

    with pytest.raises(GitHubPayloadError, match="different pull request number"):
        client.get_pull_request(
            installation_id=42,
            repository="owner/repository",
            pull_request_number=7,
        )


def test_client_configuration_requires_credentials() -> None:
    from app.core.config import Settings

    with pytest.raises(GitHubConfigurationError, match="GITHUB_APP_CLIENT_ID"):
        GitHubApiClient.from_settings(Settings())


def test_create_pull_request_review_posts_comment_for_explicit_commit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/owner/repository/pulls/7/reviews"
        assert json.loads(request.content) == {
            "body": "Automated review",
            "event": "COMMENT",
            "commit_id": "abcdef123456",
        }
        return httpx.Response(200, json=_review_response(), request=request)

    client, _ = _client(handler)
    review = client.create_pull_request_review(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
        commit_sha="abcdef123456",
        body="Automated review",
    )

    assert review.id == 9001
    assert review.state == "COMMENTED"
    assert review.commit_sha == "abcdef123456"


def test_list_pull_request_reviews_parses_review_bodies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["per_page"] == "100"
        return httpx.Response(
            200,
            json=[_review_response(body="<!-- agent-review:task=one -->")],
            request=request,
        )

    client, _ = _client(handler)
    reviews = client.list_pull_request_reviews(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
    )

    assert len(reviews) == 1
    assert reviews[0].body == "<!-- agent-review:task=one -->"


def test_create_review_refreshes_token_and_replays_json_body_after_401() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert json.loads(request.content)["event"] == "COMMENT"
        if request_count == 1:
            assert request.headers["authorization"] == "Bearer ghs_expired"
            return httpx.Response(401, request=request)
        assert request.headers["authorization"] == "Bearer ghs_refreshed"
        return httpx.Response(200, json=_review_response(), request=request)

    client, auth = _client(handler)
    auth.get_installation_token.side_effect = [
        InstallationToken(SecretStr("ghs_expired"), TOKEN_EXPIRY),
        InstallationToken(SecretStr("ghs_refreshed"), TOKEN_EXPIRY),
    ]

    client.create_pull_request_review(
        installation_id=42,
        repository="owner/repository",
        pull_request_number=7,
        commit_sha="abcdef123456",
        body="Automated review",
    )

    auth.invalidate.assert_called_once_with(42)
    assert request_count == 2
