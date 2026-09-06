from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.review_finding import ReviewFindingRecord
from app.models.review_report import ReviewReportRecord


class ReviewReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_task(
        self,
        task_id: UUID,
        *,
        for_update: bool = False,
    ) -> ReviewReportRecord | None:
        statement = (
            select(ReviewReportRecord)
            .where(ReviewReportRecord.review_task_id == task_id)
            .options(selectinload(ReviewReportRecord.findings))
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, report: ReviewReportRecord) -> ReviewReportRecord:
        self._session.add(report)
        self._session.flush()
        return report

    def replace_findings(
        self,
        report: ReviewReportRecord,
        findings: Iterable[ReviewFindingRecord],
    ) -> None:
        report.findings.clear()
        self._session.flush()
        report.findings.extend(findings)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
