from typing import Protocol, runtime_checkable

from app.domain.review.entities import ChangedFile, PullRequestSnapshot


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
