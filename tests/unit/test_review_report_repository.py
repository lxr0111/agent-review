import hashlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.review.enums import ReviewStatus
from app.domain.review.report import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewLineRange,
    ReviewReport,
    ReviewRiskLevel,
    derive_review_risk,
)
from app.models import Base
from app.models.review_finding import ReviewFindingRecord
from app.models.review_report import ReviewReportRecord
from app.models.review_task import ReviewTask
from app.repositories.review_report_repository import ReviewReportRepository
from app.services.review_report_service import ReviewReportService

GENERATED_AT = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

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
        current_stage="context_ready",
        attempt_count=1,
        version=3,
    )
    session.add(task)
    session.commit()
    return task


def _finding(
    path: str,
    *,
    title: str,
    severity: FindingSeverity,
    confidence: float = 0.91,
    location: ReviewLineRange | None = None,
) -> ReviewFinding:
    return ReviewFinding(
        title=title,
        explanation=f"{title} can produce an incorrect result.",
        suggestion=f"Add a guard for {title.lower()}.",
        file_path=path,
        severity=severity,
        category=FindingCategory.CORRECTNESS,
        confidence=confidence,
        location=location,
    )


def _report(
    findings: tuple[ReviewFinding, ...] | None = None,
    *,
    summary: str = "Two correctness problems were found.",
) -> ReviewReport:
    selected_findings = (
        findings
        if findings is not None
        else (
            _finding(
                "src/first.py",
                title="Missing empty-input guard",
                severity=FindingSeverity.HIGH,
                location=ReviewLineRange(start_line=7, end_line=8),
            ),
            _finding(
                "src/second.py",
                title="Off-by-one boundary",
                severity=FindingSeverity.MEDIUM,
            ),
        )
    )
    return ReviewReport(
        summary=summary,
        risk_level=derive_review_risk(selected_findings),
        findings=selected_findings,
        model_name="mock-review-model",
        prompt_version="review-v1",
        reviewed_file_count=2,
        skipped_file_count=1,
    )


def _service(session: Session, *, clock=lambda: GENERATED_AT) -> ReviewReportService:
    return ReviewReportService(ReviewReportRepository(session), clock=clock)


def test_save_and_load_structured_review_report(session: Session) -> None:
    task = _task(session)
    report = _report()

    record = _service(session).save(task.id, report)
    restored = _service(session).get(task.id)

    assert restored == report
    assert record.review_task_id == task.id
    assert record.generated_at == GENERATED_AT
    assert len(record.content_digest) == 64
    assert tuple(finding.position for finding in record.findings) == (0, 1)
    assert tuple(finding.file_path for finding in record.findings) == (
        "src/first.py",
        "src/second.py",
    )
    assert record.findings[0].file_path_hash == hashlib.sha256(b"src/first.py").hexdigest()
    assert record.findings[0].start_line == 7
    assert record.findings[0].end_line == 8
    assert restored is not None
    assert restored.findings[0].location == ReviewLineRange(start_line=7, end_line=8)


def test_saving_identical_report_reuses_record_without_changing_generation_time(
    session: Session,
) -> None:
    task = _task(session)
    later = GENERATED_AT + timedelta(minutes=5)
    times = iter((GENERATED_AT, later))
    service = _service(session, clock=lambda: next(times))

    first = service.save(task.id, _report())
    report_id = first.id
    second = service.save(task.id, _report())

    count = session.scalar(select(func.count()).select_from(ReviewReportRecord))
    assert count == 1
    assert second.id == report_id
    assert second.generated_at == GENERATED_AT


def test_save_supports_system_report_when_no_files_are_reviewable(session: Session) -> None:
    task = _task(session)
    report = ReviewReport(
        summary="No reviewable code changes were available.",
        risk_level=ReviewRiskLevel.NONE,
        findings=(),
        model_name=None,
        prompt_version="review-v1",
        reviewed_file_count=0,
        skipped_file_count=3,
    )

    _service(session).save(task.id, report)

    assert _service(session).get(task.id) == report
    assert session.scalar(select(func.count()).select_from(ReviewFindingRecord)) == 0


def test_saving_changed_report_replaces_findings_and_preserves_report_id(
    session: Session,
) -> None:
    task = _task(session)
    service = _service(session)
    first = service.save(task.id, _report())
    report_id = first.id
    replacement_finding = _finding(
        "src/replacement.py",
        title="Unhandled null value",
        severity=FindingSeverity.CRITICAL,
    )

    second = service.save(
        task.id,
        _report(
            (replacement_finding,),
            summary="One critical problem was found.",
        ),
    )

    finding_count = session.scalar(select(func.count()).select_from(ReviewFindingRecord))
    assert second.id == report_id
    assert second.summary == "One critical problem was found."
    assert finding_count == 1
    assert tuple(finding.file_path for finding in second.findings) == ("src/replacement.py",)


def test_finding_order_contributes_to_content_digest(session: Session) -> None:
    task = _task(session)
    service = _service(session)
    report = _report()
    first = service.save(task.id, report)
    first_digest = first.content_digest

    second = service.save(task.id, replace(report, findings=tuple(reversed(report.findings))))

    assert second.content_digest != first_digest


def test_finding_location_contributes_to_content_digest(session: Session) -> None:
    task = _task(session)
    service = _service(session)
    report = _report()
    first = service.save(task.id, report)
    first_digest = first.content_digest
    relocated = replace(
        report.findings[0],
        location=ReviewLineRange(start_line=9, end_line=9),
    )

    second = service.save(
        task.id,
        replace(report, findings=(relocated, *report.findings[1:])),
    )

    assert second.content_digest != first_digest
    assert second.findings[0].start_line == 9
    assert second.findings[0].end_line == 9


def test_save_rolls_back_all_rows_when_commit_fails(session: Session) -> None:
    task = _task(session)
    service = _service(session)

    def fail_commit(database_session: Session) -> None:
        del database_session
        raise RuntimeError("simulated commit failure")

    event.listen(session, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        service.save(task.id, _report())

    report_count = session.scalar(select(func.count()).select_from(ReviewReportRecord))
    finding_count = session.scalar(select(func.count()).select_from(ReviewFindingRecord))
    assert report_count == 0
    assert finding_count == 0


def test_invalid_report_is_rejected_before_persistence(session: Session) -> None:
    task = _task(session)
    invalid = _finding(
        "src/service.py",
        title="Invalid confidence",
        severity=FindingSeverity.HIGH,
        confidence=1.5,
    )

    with pytest.raises(ValueError, match="confidence"):
        _service(session).save(task.id, _report((invalid,)))

    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 0


def test_report_with_inconsistent_risk_is_rejected(session: Session) -> None:
    task = _task(session)
    report = replace(_report(), risk_level=ReviewRiskLevel.LOW)

    with pytest.raises(ValueError, match="risk level"):
        _service(session).save(task.id, report)

    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 0


def test_database_rejects_finding_confidence_outside_range(session: Session) -> None:
    task = _task(session)
    record = _service(session).save(task.id, _report())
    record.findings.append(
        ReviewFindingRecord(
            position=2,
            file_path="src/invalid.py",
            file_path_hash=hashlib.sha256(b"src/invalid.py").hexdigest(),
            title="Invalid confidence",
            explanation="This row bypasses service validation for a constraint test.",
            suggestion="Reject it at the database boundary.",
            severity=FindingSeverity.LOW,
            category=FindingCategory.CORRECTNESS,
            confidence=Decimal("1.5000"),
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    assert session.scalar(select(func.count()).select_from(ReviewFindingRecord)) == 2


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [
        (None, 3),
        (3, None),
        (0, 1),
        (5, 4),
    ],
)
def test_database_rejects_invalid_finding_line_range(
    session: Session,
    start_line: int | None,
    end_line: int | None,
) -> None:
    task = _task(session)
    record = _service(session).save(task.id, _report())
    record.findings.append(
        ReviewFindingRecord(
            position=2,
            file_path="src/invalid.py",
            file_path_hash=hashlib.sha256(b"src/invalid.py").hexdigest(),
            title="Invalid line range",
            explanation="This row bypasses domain validation for a constraint test.",
            suggestion="Reject it at the database boundary.",
            severity=FindingSeverity.LOW,
            category=FindingCategory.CORRECTNESS,
            confidence=Decimal("0.9000"),
            start_line=start_line,
            end_line=end_line,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    assert session.scalar(select(func.count()).select_from(ReviewFindingRecord)) == 2


def test_deleting_task_cascades_to_report_and_findings(session: Session) -> None:
    task = _task(session)
    _service(session).save(task.id, _report())

    session.delete(task)
    session.commit()

    assert session.scalar(select(func.count()).select_from(ReviewReportRecord)) == 0
    assert session.scalar(select(func.count()).select_from(ReviewFindingRecord)) == 0


def test_report_schema_does_not_store_sensitive_model_inputs(session: Session) -> None:
    assert isinstance(session.bind, Engine)
    report_columns = {
        column["name"] for column in inspect(session.bind).get_columns("review_reports")
    }
    finding_columns = {
        column["name"] for column in inspect(session.bind).get_columns("review_findings")
    }

    sensitive_columns = {"prompt", "raw_response", "patch", "api_key"}
    assert report_columns.isdisjoint(sensitive_columns)
    assert finding_columns.isdisjoint(sensitive_columns)
    assert {"start_line", "end_line"}.issubset(finding_columns)
