from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database.session import get_db_session
from app.repositories.review_task_repository import ReviewTaskRepository
from app.services.review_service import ReviewService
from app.services.webhook_service import GitHubWebhookService

DbSession = Annotated[Session, Depends(get_db_session)]


def get_review_service(session: DbSession) -> ReviewService:
    return ReviewService(ReviewTaskRepository(session))


ReviewServiceDependency = Annotated[ReviewService, Depends(get_review_service)]


def get_github_webhook_service(session: DbSession) -> GitHubWebhookService:
    return GitHubWebhookService(session)


GitHubWebhookServiceDependency = Annotated[
    GitHubWebhookService, Depends(get_github_webhook_service)
]
