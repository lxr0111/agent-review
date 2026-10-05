from app.domain.review.entities import (
    ChangedFile,
    PullRequestReview,
    PullRequestReviewComment,
    PullRequestSnapshot,
)
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

    def list_pull_request_reviews(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[PullRequestReview, ...]:
        return ()

    def create_pull_request_review(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        commit_sha: str,
        body: str,
        comments: tuple[PullRequestReviewComment, ...] = (),
    ) -> PullRequestReview:
        del comments
        return PullRequestReview(
            id=1,
            commit_sha=commit_sha,
            state="COMMENTED",
            body=body,
            html_url=None,
        )


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
