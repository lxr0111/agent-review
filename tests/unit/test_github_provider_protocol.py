from app.domain.review.entities import ChangedFile, PullRequestSnapshot
from app.providers.github import GitHubProvider


class FakeGitHubProvider:
    def get_pull_request(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> PullRequestSnapshot:
        return PullRequestSnapshot(
            repository=repository,
            number=pull_request_number,
            title="Add webhook ingestion",
            body=None,
            base_sha="123456abcdef",
            head_sha="abcdef123456",
            draft=False,
            author_login="octocat",
        )

    def list_pull_request_files(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[ChangedFile, ...]:
        return ()


def test_provider_protocol_supports_test_doubles() -> None:
    provider = FakeGitHubProvider()

    assert isinstance(provider, GitHubProvider)
    assert (
        provider.get_pull_request(
            installation_id=1,
            repository="owner/repository",
            pull_request_number=7,
        ).head_sha
        == "abcdef123456"
    )
