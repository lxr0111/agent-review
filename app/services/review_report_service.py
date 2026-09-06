import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.review.report import ReviewFinding, ReviewReport, derive_review_risk
from app.models.review_finding import ReviewFindingRecord
from app.models.review_report import ReviewReportRecord
from app.repositories.review_report_repository import ReviewReportRepository


class ReviewReportService:
    def __init__(
        self,
        repository: ReviewReportRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def save(self, task_id: UUID, report: ReviewReport) -> ReviewReportRecord:
        _validate_report(report)
        content_digest = _report_digest(report)
        generated_at = self._now()

        try:
            record = self._repository.get_for_task(task_id, for_update=True)
            if record is not None and record.content_digest == content_digest:
                self._repository.commit()
                return record

            finding_records = [
                _finding_record(finding, position)
                for position, finding in enumerate(report.findings)
            ]
            if record is None:
                record = ReviewReportRecord(review_task_id=task_id)
                _update_record(record, report, content_digest, generated_at)
                record.findings.extend(finding_records)
                self._repository.add(record)
            else:
                _update_record(record, report, content_digest, generated_at)
                self._repository.replace_findings(record, finding_records)

            self._repository.commit()
            return record
        except Exception:
            self._repository.rollback()
            raise

    def get(self, task_id: UUID) -> ReviewReport | None:
        record = self._repository.get_for_task(task_id)
        if record is None:
            return None
        return ReviewReport(
            summary=record.summary,
            risk_level=record.risk_level,
            findings=tuple(
                ReviewFinding(
                    title=finding.title,
                    explanation=finding.explanation,
                    suggestion=finding.suggestion,
                    file_path=finding.file_path,
                    severity=finding.severity,
                    category=finding.category,
                    confidence=float(finding.confidence),
                )
                for finding in record.findings
            ),
            model_name=record.model_name,
            prompt_version=record.prompt_version,
            reviewed_file_count=record.reviewed_file_count,
            skipped_file_count=record.skipped_file_count,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Review report clock must be timezone-aware")
        return now.astimezone(UTC)


def _update_record(
    record: ReviewReportRecord,
    report: ReviewReport,
    content_digest: str,
    generated_at: datetime,
) -> None:
    record.summary = report.summary
    record.risk_level = report.risk_level
    record.model_name = report.model_name
    record.prompt_version = report.prompt_version
    record.reviewed_file_count = report.reviewed_file_count
    record.skipped_file_count = report.skipped_file_count
    record.content_digest = content_digest
    record.generated_at = generated_at


def _finding_record(finding: ReviewFinding, position: int) -> ReviewFindingRecord:
    return ReviewFindingRecord(
        position=position,
        file_path=finding.file_path,
        file_path_hash=hashlib.sha256(finding.file_path.encode("utf-8")).hexdigest(),
        title=finding.title,
        explanation=finding.explanation,
        suggestion=finding.suggestion,
        severity=finding.severity,
        category=finding.category,
        confidence=Decimal(str(finding.confidence)),
    )


def _report_digest(report: ReviewReport) -> str:
    payload = {
        "summary": report.summary,
        "risk_level": report.risk_level.value,
        "model_name": report.model_name,
        "prompt_version": report.prompt_version,
        "reviewed_file_count": report.reviewed_file_count,
        "skipped_file_count": report.skipped_file_count,
        "findings": [
            {
                "title": finding.title,
                "explanation": finding.explanation,
                "suggestion": finding.suggestion,
                "file_path": finding.file_path,
                "severity": finding.severity.value,
                "category": finding.category.value,
                "confidence": finding.confidence,
            }
            for finding in report.findings
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_report(report: ReviewReport) -> None:
    if not report.summary.strip():
        raise ValueError("Review report summary must not be empty")
    if not report.prompt_version.strip() or len(report.prompt_version) > 50:
        raise ValueError("Review report prompt version must contain at most 50 characters")
    if report.model_name is not None and len(report.model_name) > 255:
        raise ValueError("Review report model name must contain at most 255 characters")
    if report.reviewed_file_count < 0 or report.skipped_file_count < 0:
        raise ValueError("Review report file counts must not be negative")
    if report.findings and report.reviewed_file_count == 0:
        raise ValueError("A report with findings must include at least one reviewed file")
    finding_file_count = len({finding.file_path for finding in report.findings})
    if finding_file_count > report.reviewed_file_count:
        raise ValueError("Review findings reference more files than were reviewed")
    if report.risk_level != derive_review_risk(report.findings):
        raise ValueError("Review report risk level does not match its findings")

    for finding in report.findings:
        if not finding.file_path.strip():
            raise ValueError("Review finding file path must not be empty")
        if not finding.title.strip() or len(finding.title) > 200:
            raise ValueError("Review finding title must contain at most 200 characters")
        if not finding.explanation.strip() or not finding.suggestion.strip():
            raise ValueError("Review finding explanation and suggestion must not be empty")
        if not 0 <= finding.confidence <= 1:
            raise ValueError("Review finding confidence must be between 0 and 1")
