from dataclasses import dataclass
from uuid import UUID

from app.domain.review.enums import ReviewStatus


@dataclass(frozen=True, slots=True)
class ReviewTaskIdentity:
    id: UUID
    repository: str
    pull_request_number: int
    head_sha: str
    status: ReviewStatus
