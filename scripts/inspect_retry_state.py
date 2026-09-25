"""Print safe, read-only retry state for one review task."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.engine import create_database_engine
from app.models.review_publication import ReviewPublicationRecord
from app.models.review_report import ReviewReportRecord
from app.models.review_task import ReviewTask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", type=UUID)
    parser.add_argument("--repository")
    parser.add_argument("--pr", type=int)
    args = parser.parse_args()

    engine = create_database_engine()
    connection = engine.connect()
    try:
        connection.exec_driver_sql("START TRANSACTION READ ONLY")
        session = Session(bind=connection)
        try:
            task = _find_task(
                session,
                task_id=args.task_id,
                repository=args.repository,
                pull_request_number=args.pr,
            )
            if task is None:
                raise SystemExit("No matching review task was found")
            print(json.dumps(_snapshot(session, task), default=str, ensure_ascii=False, indent=2))
        finally:
            session.close()
            connection.rollback()
    finally:
        connection.close()
        engine.dispose()


def _find_task(
    session: Session,
    *,
    task_id: UUID | None,
    repository: str | None,
    pull_request_number: int | None,
) -> ReviewTask | None:
    statement = select(ReviewTask)
    if task_id is not None:
        statement = statement.where(ReviewTask.id == task_id)
    else:
        if repository is not None:
            statement = statement.where(ReviewTask.repository == repository)
        if pull_request_number is not None:
            statement = statement.where(ReviewTask.pull_request_number == pull_request_number)
        statement = statement.order_by(ReviewTask.created_at.desc()).limit(1)
    return session.scalar(statement)


def _snapshot(session: Session, task: ReviewTask) -> dict[str, object]:
    report = session.scalar(
        select(ReviewReportRecord).where(ReviewReportRecord.review_task_id == task.id)
    )
    publication = None
    if report is not None:
        publication = session.scalar(
            select(ReviewPublicationRecord).where(
                ReviewPublicationRecord.review_report_id == report.id
            )
        )

    report_count = (
        session.scalar(
            select(func.count())
            .select_from(ReviewReportRecord)
            .where(ReviewReportRecord.review_task_id == task.id)
        )
        or 0
    )
    publication_count = 0
    if report is not None:
        publication_count = (
            session.scalar(
                select(func.count())
                .select_from(ReviewPublicationRecord)
                .where(ReviewPublicationRecord.review_report_id == report.id)
            )
            or 0
        )

    return {
        "task": {
            "id": task.id,
            "repository": task.repository,
            "pull_request_number": task.pull_request_number,
            "head_sha": task.head_sha,
            "status": task.status.value,
            "current_stage": task.current_stage,
            "attempt_count": task.attempt_count,
            "error_code": task.error_code,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "finished_at": task.finished_at,
        },
        "report": {
            "count": report_count,
            "model_name": report.model_name if report is not None else None,
            "prompt_version": report.prompt_version if report is not None else None,
            "generated_at": report.generated_at if report is not None else None,
        },
        "publication": {
            "count": publication_count,
            "status": publication.status.value if publication is not None else None,
            "attempt_count": publication.attempt_count if publication is not None else None,
            "external_review_id": (
                publication.external_review_id if publication is not None else None
            ),
            "published_at": publication.published_at if publication is not None else None,
        },
    }


if __name__ == "__main__":
    main()
