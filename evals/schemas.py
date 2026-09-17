from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.review.entities import ChangedFile, PullRequestSnapshot, ReviewContext
from app.domain.review.enums import FileChangeStatus
from app.domain.review.report import FindingCategory, FindingSeverity


class EvalLineRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class EvalPullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository: str = Field(min_length=1)
    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    body: str | None = None
    base_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    draft: bool = False
    author_login: str | None = None

    def to_domain(self) -> PullRequestSnapshot:
        return PullRequestSnapshot(
            repository=self.repository,
            number=self.number,
            title=self.title,
            body=self.body,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            draft=self.draft,
            author_login=self.author_login,
        )


class EvalChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1)
    status: FileChangeStatus
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str = Field(min_length=1)
    previous_path: str | None = None

    def to_domain(self) -> ChangedFile:
        return ChangedFile(
            path=self.path,
            status=self.status,
            additions=self.additions,
            deletions=self.deletions,
            changes=self.changes,
            patch=self.patch,
            previous_path=self.previous_path,
        )


class EvalExpectedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(min_length=1, max_length=500)
    category: FindingCategory
    severity: FindingSeverity
    file_path: str = Field(min_length=1)
    range: EvalLineRange
    match_terms: tuple[tuple[str, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_match_terms(self) -> Self:
        for alternatives in self.match_terms:
            if not alternatives or any(not term.strip() for term in alternatives):
                raise ValueError("each match term group must contain non-empty alternatives")
        return self


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(min_length=1, max_length=500)
    pull_request: EvalPullRequest
    files: tuple[EvalChangedFile, ...] = Field(min_length=1)
    expected_findings: tuple[EvalExpectedFinding, ...]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        file_paths = [changed_file.path for changed_file in self.files]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("case file paths must be unique")

        finding_ids = [finding.id for finding in self.expected_findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("expected finding IDs must be unique within a case")

        unknown_paths = {
            finding.file_path
            for finding in self.expected_findings
            if finding.file_path not in file_paths
        }
        if unknown_paths:
            raise ValueError(
                "expected findings reference unknown files: " + ", ".join(sorted(unknown_paths))
            )
        return self

    @property
    def is_negative(self) -> bool:
        return not self.expected_findings

    def to_review_context(self) -> ReviewContext:
        files = tuple(changed_file.to_domain() for changed_file in self.files)
        return ReviewContext(
            pull_request=self.pull_request.to_domain(),
            reviewable_files=files,
            skipped_files=(),
            total_patch_characters=sum(len(changed_file.patch) for changed_file in self.files),
        )


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["review-eval-v1"]
    dataset_version: str = Field(min_length=1, max_length=50)
    cases: tuple[EvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> Self:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case IDs must be unique")
        return self


def load_dataset(path: Path) -> EvalDataset:
    return EvalDataset.model_validate_json(path.read_text(encoding="utf-8"))
