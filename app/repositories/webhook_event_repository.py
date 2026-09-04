from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.webhook_event import WebhookEvent


class WebhookEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: WebhookEvent) -> WebhookEvent:
        self._session.add(event)
        self._session.flush()
        return event

    def get_by_delivery(self, provider: str, delivery_id: str) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(
            WebhookEvent.provider == provider,
            WebhookEvent.delivery_id == delivery_id,
        )
        return self._session.scalar(statement)
