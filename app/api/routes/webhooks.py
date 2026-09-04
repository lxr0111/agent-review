from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from app.api.dependencies import GitHubWebhookServiceDependency
from app.core.config import get_settings
from app.core.exceptions import WebhookNotConfigured
from app.core.security import verify_github_signature
from app.schemas.webhook import WebhookResponse
from app.workers.review_tasks import run_review

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/github",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_github_webhook(
    request: Request,
    service: GitHubWebhookServiceDependency,
    github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
    github_signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> WebhookResponse:
    settings = get_settings()
    if settings.github_webhook_secret is None:
        raise WebhookNotConfigured("GITHUB_WEBHOOK_SECRET is not configured")

    raw_body = await request.body()
    verify_github_signature(
        raw_body,
        github_signature,
        settings.github_webhook_secret.get_secret_value(),
    )
    result = service.handle(github_delivery or "", github_event or "", raw_body)

    if result.should_enqueue and result.review_task_id is not None:
        run_review.delay(str(result.review_task_id))

    return WebhookResponse(
        status=result.status,
        delivery_id=result.delivery_id,
        review_task_id=result.review_task_id,
    )
