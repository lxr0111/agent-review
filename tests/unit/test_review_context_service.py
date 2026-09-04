import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.review.entities import ChangedFile, PullRequestSnapshot
from app.domain.review.enums import FileChangeStatus, FileSkipReason, ReviewStatus
from app.domain.review.exceptions import (
    DuplicateChangedFilePath,
    MissingGitHubInstallation,
    PullRequestHeadChanged,
    PullRequestIdentityMismatch,
)
from app.models import Base
from app.models.review_task import ReviewTask
from app.repositories.review_file_repository import ReviewFileRepository
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_context_service import ReviewContextService

EXPECTED_HEAD_SHA = "abcdef123456"
BASE_SHA = "123456abcdef"
RECORDED_AT = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as database_session:
        yield database_session
    engine.dispose()


class FakeGitHubProvider:
    def __init__(
        self,
        session: Session,
        *,
        pull_request: PullRequestSnapshot | None = None,
        files: tuple[ChangedFile, ...] = (),
    ) -> None:
        self._session = session
        self.pull_request = pull_request or _pull_request()
        self.files = files
        self.get_calls = 0
        self.list_calls = 0

    def get_pull_request(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> PullRequestSnapshot:
        assert installation_id == 42
        assert repository == "owner/repository"
        assert pull_request_number == 7
        assert not self._session.in_transaction()
        self.get_calls += 1
        return self.pull_request

    def list_pull_request_files(
        self,
        *,
        installation_id: int,
        repository: str,
        pull_request_number: int,
    ) -> tuple[ChangedFile, ...]:
        assert installation_id == 42
        assert repository == "owner/repository"
        assert pull_request_number == 7
        assert not self._session.in_transaction()
        self.list_calls += 1
        return self.files


def _pull_request(*, head_sha: str = EXPECTED_HEAD_SHA) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        repository="owner/repository",
        number=7,
        title="Add review context",
        body="Fetch and filter pull request files.",
        base_sha=BASE_SHA,
        head_sha=head_sha,
        draft=False,
        author_login="octocat",
    )


def _file(
    path: str,
    *,
    patch: str | None = "@@ -1 +1 @@\n-old\n+new",
    changes: int = 2,
) -> ChangedFile:
    return ChangedFile(
        path=path,
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=changes,
        patch=patch,
    )


def _task(session: Session, *, installation_id: int | None = 42) -> ReviewTask:
    task = ReviewTask(
        repository="owner/repository",
        github_repository_id=1001,
        installation_id=installation_id,
        pull_request_number=7,
        base_sha="old-base-sha",
        head_sha=EXPECTED_HEAD_SHA,
        status=ReviewStatus.QUEUED,
        current_stage="queued",
        attempt_count=0,
        version=2,
    )
    session.add(task)
    session.commit()
    return task


def _service(
    session: Session,
    github: FakeGitHubProvider,
    *,
    max_total_patch_characters: int = 300_000,
) -> ReviewContextService:
    return ReviewContextService(
        review_tasks=ReviewTaskRepository(session),
        review_files=ReviewFileRepository(session),
        github=github,
        max_total_patch_characters=max_total_patch_characters,
        clock=lambda: RECORDED_AT,
    )


def test_build_fetches_filters_and_persists_review_context(session: Session) -> None:
    task = _task(session)
    source = _file("src/service.py")
    lock_file = _file("frontend/package-lock.json")
    binary = _file("assets/logo.png", patch=None)
    github = FakeGitHubProvider(session, files=(source, lock_file, binary))

    context = _service(session, github).build(task.id)

    assert context.pull_request == github.pull_request
    assert context.reviewable_files == (source,)
    assert tuple(item.reason for item in context.skipped_files) == (
        FileSkipReason.EXCLUDED_PATH,
        FileSkipReason.MISSING_PATCH,
    )
    assert context.total_patch_characters == len(source.patch or "")
    assert github.get_calls == 1
    assert github.list_calls == 1

    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == ReviewStatus.RUNNING
    assert stored_task.current_stage == "context_ready"
    assert stored_task.base_sha == BASE_SHA
    assert stored_task.attempt_count == 1

    records = ReviewFileRepository(session).list_for_task(task.id)
    assert tuple(record.path for record in records) == (
        "src/service.py",
        "frontend/package-lock.json",
        "assets/logo.png",
    )
    assert tuple(record.selected for record in records) == (True, False, False)
    assert (
        records[0].patch_digest == hashlib.sha256((source.patch or "").encode("utf-8")).hexdigest()
    )
    assert records[0].recorded_at == RECORDED_AT.replace(tzinfo=None)


def test_build_applies_total_patch_budget(session: Session) -> None:
    task = _task(session)
    first = _file("src/first.py", patch="1234")
    second = _file("src/second.py", patch="5678")
    github = FakeGitHubProvider(session, files=(first, second))

    context = _service(
        session,
        github,
        max_total_patch_characters=4,
    ).build(task.id)

    assert context.reviewable_files == (first,)
    assert context.skipped_files[0].file is second
    assert context.skipped_files[0].reason == FileSkipReason.TOTAL_BUDGET_EXCEEDED
    records = ReviewFileRepository(session).list_for_task(task.id)
    assert records[1].skip_reason == FileSkipReason.TOTAL_BUDGET_EXCEEDED


def test_build_marks_task_stale_when_pull_request_head_changed(session: Session) -> None:
    task = _task(session)
    github = FakeGitHubProvider(
        session,
        pull_request=_pull_request(head_sha="newer-head-sha"),
        files=(_file("src/service.py"),),
    )

    with pytest.raises(PullRequestHeadChanged) as error:
        _service(session, github).build(task.id)

    assert error.value.expected_head_sha == EXPECTED_HEAD_SHA
    assert error.value.actual_head_sha == "newer-head-sha"
    assert github.list_calls == 0
    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == ReviewStatus.STALE
    assert stored_task.error_code == "pull_request_head_changed"
    assert ReviewFileRepository(session).list_for_task(task.id) == ()


def test_build_fails_task_without_installation_id(session: Session) -> None:
    task = _task(session, installation_id=None)
    github = FakeGitHubProvider(session)

    with pytest.raises(MissingGitHubInstallation):
        _service(session, github).build(task.id)

    assert github.get_calls == 0
    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == ReviewStatus.FAILED
    assert stored_task.error_code == "github_installation_missing"


def test_build_fails_task_when_provider_returns_different_pull_request(
    session: Session,
) -> None:
    task = _task(session)
    snapshot = _pull_request()
    github = FakeGitHubProvider(
        session,
        pull_request=PullRequestSnapshot(
            repository="another/repository",
            number=snapshot.number,
            title=snapshot.title,
            body=snapshot.body,
            base_sha=snapshot.base_sha,
            head_sha=snapshot.head_sha,
            draft=snapshot.draft,
            author_login=snapshot.author_login,
        ),
    )

    with pytest.raises(PullRequestIdentityMismatch):
        _service(session, github).build(task.id)

    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == ReviewStatus.FAILED
    assert stored_task.error_code == "pull_request_identity_mismatch"


def test_build_fails_task_when_provider_returns_duplicate_paths(session: Session) -> None:
    task = _task(session)
    duplicate = _file("src/service.py")
    github = FakeGitHubProvider(session, files=(duplicate, duplicate))

    with pytest.raises(DuplicateChangedFilePath):
        _service(session, github).build(task.id)

    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == ReviewStatus.FAILED
    assert stored_task.error_code == "duplicate_changed_file_path"
    assert ReviewFileRepository(session).list_for_task(task.id) == ()


def test_rebuilding_context_replaces_previous_file_metadata(session: Session) -> None:
    task = _task(session)
    github = FakeGitHubProvider(session, files=(_file("src/old.py"),))
    service = _service(session, github)
    service.build(task.id)

    github.files = (_file("src/new.py"),)
    service.build(task.id)

    records = ReviewFileRepository(session).list_for_task(task.id)
    assert tuple(record.path for record in records) == ("src/new.py",)
    stored_task = ReviewTaskRepository(session).get(task.id)
    assert stored_task is not None
    assert stored_task.attempt_count == 1
    assert stored_task.current_stage == "context_ready"
