from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.review.enums import ReviewStatus


class ReviewCreate(BaseModel):
    repository: str = Field(min_length=1, max_length=255, examples=["owner/repository"])
    pull_request_number: int = Field(gt=0)
    head_sha: str = Field(min_length=7, max_length=64)


class ReviewTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository: str
    github_repository_id: int | None
    installation_id: int | None
    pull_request_number: int
    base_sha: str | None
    head_sha: str
    delivery_id: str | None
    status: ReviewStatus
    current_stage: str
    attempt_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
