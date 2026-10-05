from dataclasses import dataclass
from uuid import UUID

from app.domain.review.enums import FileChangeStatus, FileSkipReason, ReviewStatus


@dataclass(frozen=True, slots=True)
class ReviewTaskIdentity:
    id: UUID
    repository: str
    pull_request_number: int
    head_sha: str
    status: ReviewStatus


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    repository: str
    number: int
    title: str
    base_sha: str
    head_sha: str
    draft: bool
    author_login: str | None
    body: str | None = None


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    status: FileChangeStatus
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    previous_path: str | None = None


@dataclass(frozen=True, slots=True)
class SkippedFile:
    file: ChangedFile
    reason: FileSkipReason


@dataclass(frozen=True, slots=True)
class FileSelection:
    reviewable: tuple[ChangedFile, ...]
    skipped: tuple[SkippedFile, ...]


@dataclass(frozen=True, slots=True)
class ReviewContext:
    pull_request: PullRequestSnapshot
    reviewable_files: tuple[ChangedFile, ...]
    skipped_files: tuple[SkippedFile, ...]
    total_patch_characters: int


@dataclass(frozen=True, slots=True)
class PullRequestReview:
    id: int
    commit_sha: str
    state: str
    body: str | None
    html_url: str | None


@dataclass(frozen=True, slots=True)
class PullRequestReviewComment:
    path: str
    body: str
    line: int
    start_line: int | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("Pull request review comment path must not be empty")
        if not self.body.strip():
            raise ValueError("Pull request review comment body must not be empty")
        if self.line < 1:
            raise ValueError("Pull request review comment line must be positive")
        if self.start_line is not None and not 1 <= self.start_line < self.line:
            raise ValueError("Pull request review comment start line must precede the end line")
