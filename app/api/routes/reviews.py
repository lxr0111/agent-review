from uuid import UUID

from fastapi import APIRouter, status

from app.api.dependencies import ReviewServiceDependency
from app.schemas.review import ReviewCreate, ReviewTaskResponse
from app.workers.review_tasks import run_review

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def create_review(payload: ReviewCreate, service: ReviewServiceDependency) -> ReviewTaskResponse:
    task = service.create(payload)
    run_review.delay(str(task.id))
    return ReviewTaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=ReviewTaskResponse)
def get_review(task_id: UUID, service: ReviewServiceDependency) -> ReviewTaskResponse:
    return ReviewTaskResponse.model_validate(service.get(task_id))
