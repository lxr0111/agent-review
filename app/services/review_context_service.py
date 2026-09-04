import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import InvalidStateTransition, ReviewTaskNotFound
from app.domain.review.entities import (
    ChangedFile,
    FileSelection,
    ReviewContext,
)
from app.domain.review.enums import ReviewStatus
from app.domain.review.exceptions import (
    DuplicateChangedFilePath,
    MissingGitHubInstallation,
    PullRequestHeadChanged,
    PullRequestIdentityMismatch,
)
from app.domain.review.file_filter import (
    FileFilterPolicy,
    apply_total_patch_budget,
    select_reviewable_files,
)
from app.models.review_file import ReviewFile
from app.providers.github.protocol import GitHubProvider
from app.repositories.review_file_repository import ReviewFileRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_service import ReviewService


class ReviewContextService:
    def __init__(
        self,
        *,
        review_tasks: ReviewTaskRepository,
        review_files: ReviewFileRepository,
        github: GitHubProvider,
        file_policy: FileFilterPolicy | None = None,
        max_total_patch_characters: int = 300_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_total_patch_characters < 1:
            raise ValueError("Total patch character budget must be positive")
        self._review_tasks = review_tasks
        self._review_files = review_files
        self._github = github
        self._file_policy = file_policy or FileFilterPolicy()
        self._max_total_patch_characters = max_total_patch_characters
        self._clock = clock or (lambda: datetime.now(UTC))
        self._review_service = ReviewService(review_tasks)

    def build(self, task_id: UUID) -> ReviewContext:
        task = self._review_service.start(task_id, stage="fetching_context")
        if task.installation_id is None:
            message = "Review task does not have a GitHub installation ID"
            self._review_service.fail(task_id, "github_installation_missing", message)
            raise MissingGitHubInstallation(message)

        installation_id = task.installation_id
        repository = task.repository
        pull_request_number = task.pull_request_number
        expected_head_sha = task.head_sha

        pull_request = self._github.get_pull_request(
            installation_id=installation_id,
            repository=repository,
            pull_request_number=pull_request_number,
        )
        if pull_request.repository != repository or pull_request.number != pull_request_number:
            identity_error = PullRequestIdentityMismatch(
                "GitHub provider returned a different pull request identity"
            )
            self._review_service.fail(
                task_id,
                "pull_request_identity_mismatch",
                str(identity_error),
            )
            raise identity_error
        if pull_request.head_sha != expected_head_sha:
            head_error = PullRequestHeadChanged(expected_head_sha, pull_request.head_sha)
            self._review_service.mark_stale(
                task_id,
                "pull_request_head_changed",
                str(head_error),
            )
            raise head_error

        changed_files = self._github.list_pull_request_files(
            installation_id=installation_id,
            repository=repository,
            pull_request_number=pull_request_number,
        )
        try:
            _ensure_unique_paths(changed_files)
        except DuplicateChangedFilePath as error:
            self._review_service.fail(
                task_id,
                "duplicate_changed_file_path",
                str(error),
            )
            raise
        selection = select_reviewable_files(changed_files, self._file_policy)
        selection = apply_total_patch_budget(
            selection,
            self._max_total_patch_characters,
        )
        context = ReviewContext(
            pull_request=pull_request,
            reviewable_files=selection.reviewable,
            skipped_files=selection.skipped,
            total_patch_characters=sum(
                len(changed_file.patch or "") for changed_file in selection.reviewable
            ),
        )
        self._persist_context(
            task_id,
            expected_head_sha,
            pull_request.base_sha,
            changed_files,
            selection,
        )
        return context

    def _persist_context(
        self,
        task_id: UUID,
        expected_head_sha: str,
        base_sha: str,
        changed_files: tuple[ChangedFile, ...],
        selection: FileSelection,
    ) -> None:
        recorded_at = self._now()
        try:
            task = self._review_tasks.get(task_id, for_update=True)
            if task is None:
                raise ReviewTaskNotFound(f"Review task {task_id} was not found")
            if task.status != ReviewStatus.RUNNING:
                raise InvalidStateTransition(
                    f"Cannot store review context while task is {task.status}"
                )
            if task.head_sha != expected_head_sha:
                raise PullRequestHeadChanged(expected_head_sha, task.head_sha)

            reviewable = set(selection.reviewable)
            skip_reasons = {skipped.file: skipped.reason for skipped in selection.skipped}
            records = [
                ReviewFile(
                    review_task_id=task_id,
                    path=changed_file.path,
                    path_hash=_sha256_text(changed_file.path),
                    position=position,
                    previous_path=changed_file.previous_path,
                    status=changed_file.status,
                    additions=changed_file.additions,
                    deletions=changed_file.deletions,
                    changes=changed_file.changes,
                    patch_digest=(
                        _sha256_text(changed_file.patch) if changed_file.patch is not None else None
                    ),
                    selected=changed_file in reviewable,
                    skip_reason=skip_reasons.get(changed_file),
                    recorded_at=recorded_at,
                )
                for position, changed_file in enumerate(changed_files)
            ]

            self._review_files.replace_for_task(task_id, records)
            task.base_sha = base_sha
            task.current_stage = "context_ready"
            task.version += 1
            self._review_tasks.commit()
        except Exception:
            self._review_tasks.rollback()
            raise

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Review context clock must be timezone-aware")
        return now.astimezone(UTC)


def _ensure_unique_paths(changed_files: tuple[ChangedFile, ...]) -> None:
    paths = [changed_file.path for changed_file in changed_files]
    if len(paths) != len(set(paths)):
        raise DuplicateChangedFilePath("GitHub returned duplicate changed file paths")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
