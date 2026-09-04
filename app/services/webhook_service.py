import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidWebhookPayload
from app.domain.review.enums import WebhookEventStatus
from app.models.webhook_event import WebhookEvent
from app.providers.github.webhook import get_webhook_action, parse_webhook_json
from app.repositories.review_task_repository import ReviewTaskRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.schemas.webhook import GitHubPullRequestPayload
from app.services.review_service import ReviewService

SUPPORTED_PULL_REQUEST_ACTIONS = frozenset({"opened", "synchronize", "reopened"})


@dataclass(frozen=True, slots=True)
class WebhookHandlingResult:
    status: Literal["accepted", "duplicate", "ignored", "pong"]
    delivery_id: str
    review_task_id: UUID | None
    should_enqueue: bool = False


class GitHubWebhookService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = WebhookEventRepository(session)
        self._reviews = ReviewTaskRepository(session)
        self._review_service = ReviewService(self._reviews)

    def handle(self, delivery_id: str, event_type: str, raw_body: bytes) -> WebhookHandlingResult:
        if not delivery_id.strip():
            raise InvalidWebhookPayload("Missing X-GitHub-Delivery header")
        if len(delivery_id) > 100:
            raise InvalidWebhookPayload("X-GitHub-Delivery header is too long")
        if not event_type.strip():
            raise InvalidWebhookPayload("Missing X-GitHub-Event header")
        if len(event_type) > 100:
            raise InvalidWebhookPayload("X-GitHub-Event header is too long")

        digest = hashlib.sha256(raw_body).hexdigest()
        existing = self._events.get_by_delivery("github", delivery_id)
        if existing is not None:
            return self._duplicate_result(existing, event_type, digest)

        payload = parse_webhook_json(raw_body)
        action = get_webhook_action(payload)

        if event_type == "ping":
            return self._record_ignored(delivery_id, event_type, action, digest, "pong")

        if event_type != "pull_request" or action not in SUPPORTED_PULL_REQUEST_ACTIONS:
            return self._record_ignored(delivery_id, event_type, action, digest, "ignored")

        try:
            pull_request = GitHubPullRequestPayload.model_validate(payload)
        except ValidationError as exc:
            raise InvalidWebhookPayload("Invalid GitHub pull_request payload") from exc

        if pull_request.number != pull_request.pull_request.number:
            raise InvalidWebhookPayload("Pull request number is inconsistent")

        event = self._new_event(
            delivery_id=delivery_id,
            event_type=event_type,
            action=action,
            digest=digest,
            status=WebhookEventStatus.ACCEPTED,
        )
        try:
            self._events.add(event)
            task = self._reviews.get_by_identity(
                pull_request.repository.full_name,
                pull_request.number,
                pull_request.pull_request.head.sha,
            )
            task_created = task is None
            if task is None:
                task = self._review_service.create_from_github(pull_request, delivery_id)
            event.review_task_id = task.id
            event.processed_at = datetime.now(UTC)
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            duplicate = self._events.get_by_delivery("github", delivery_id)
            if duplicate is None:
                raise
            return self._duplicate_result(duplicate, event_type, digest)

        return WebhookHandlingResult(
            status="accepted" if task_created else "duplicate",
            delivery_id=delivery_id,
            review_task_id=task.id,
            should_enqueue=task_created,
        )

    def _record_ignored(
        self,
        delivery_id: str,
        event_type: str,
        action: str | None,
        digest: str,
        response_status: Literal["ignored", "pong"],
    ) -> WebhookHandlingResult:
        event = self._new_event(
            delivery_id=delivery_id,
            event_type=event_type,
            action=action,
            digest=digest,
            status=WebhookEventStatus.IGNORED,
        )
        try:
            self._events.add(event)
            event.processed_at = datetime.now(UTC)
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            duplicate = self._events.get_by_delivery("github", delivery_id)
            if duplicate is None:
                raise
            return self._duplicate_result(duplicate, event_type, digest)
        return WebhookHandlingResult(response_status, delivery_id, None)

    @staticmethod
    def _duplicate_result(
        existing: WebhookEvent,
        event_type: str,
        digest: str,
    ) -> WebhookHandlingResult:
        if existing.event_type != event_type or existing.payload_digest != digest:
            raise InvalidWebhookPayload(
                "GitHub delivery ID was reused with different event content"
            )
        return WebhookHandlingResult(
            status="duplicate",
            delivery_id=existing.delivery_id,
            review_task_id=existing.review_task_id,
        )

    @staticmethod
    def _new_event(
        delivery_id: str,
        event_type: str,
        action: str | None,
        digest: str,
        status: WebhookEventStatus,
    ) -> WebhookEvent:
        return WebhookEvent(
            provider="github",
            delivery_id=delivery_id,
            event_type=event_type,
            action=action,
            payload_digest=digest,
            status=status,
        )
