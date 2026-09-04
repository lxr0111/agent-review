import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.review.enums import FileChangeStatus, FileSkipReason, ReviewStatus
from app.models import Base
from app.models.review_file import ReviewFile
from app.models.review_task import ReviewTask
from app.repositories.review_file_repository import ReviewFileRepository


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as database_session:
        yield database_session
    engine.dispose()


def _task(session: Session) -> ReviewTask:
    task = ReviewTask(
        repository="owner/repository",
        pull_request_number=7,
        head_sha="abcdef123456",
        status=ReviewStatus.RUNNING,
        current_stage="fetching_context",
        attempt_count=1,
        version=2,
    )
    session.add(task)
    session.flush()
    return task


def _file(
    task: ReviewTask,
    path: str,
    *,
    position: int = 0,
    selected: bool = True,
) -> ReviewFile:
    return ReviewFile(
        review_task_id=task.id,
        path=path,
        path_hash=hashlib.sha256(path.encode()).hexdigest(),
        position=position,
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=2,
        patch_digest=hashlib.sha256(b"patch content").hexdigest(),
        selected=selected,
        skip_reason=None if selected else FileSkipReason.TOO_LARGE,
        recorded_at=datetime.now(UTC),
    )


def test_replace_for_task_is_idempotent(session: Session) -> None:
    task = _task(session)
    repository = ReviewFileRepository(session)

    repository.replace_for_task(task.id, [_file(task, "src/old.py")])
    repository.replace_for_task(
        task.id,
        [
            _file(task, "src/new.py", position=0),
            _file(task, "package-lock.json", position=1, selected=False),
        ],
    )
    session.commit()

    records = repository.list_for_task(task.id)
    count = session.scalar(select(func.count()).select_from(ReviewFile))
    assert count == 2
    assert tuple(record.path for record in records) == (
        "src/new.py",
        "package-lock.json",
    )


def test_review_file_schema_does_not_persist_patch_content(session: Session) -> None:
    columns = {column["name"] for column in inspect(session.bind).get_columns("review_files")}

    assert "patch" not in columns
    assert "patch_digest" in columns
