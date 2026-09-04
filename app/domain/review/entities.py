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
    author_login: str
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
