from typing import Protocol, runtime_checkable

from app.domain.review.entities import (
    ChangedFile,
    PullRequestReview,
    PullRequestReviewComment,
    PullRequestSnapshot,
)


@runtime_checkable
class GitHubProvider(Protocol):
    def get_pull_request(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> PullRequestSnapshot: ...

    def list_pull_request_files(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[ChangedFile, ...]: ...

    def list_pull_request_reviews(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[PullRequestReview, ...]: ...

    def create_pull_request_review(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        commit_sha: str,
        body: str,
        comments: tuple[PullRequestReviewComment, ...] = (),
    ) -> PullRequestReview: ...
