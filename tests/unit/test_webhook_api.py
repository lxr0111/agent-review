import hashlib
import hmac
import json
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_github_webhook_service
from app.api.routes import webhooks
from app.core.config import Settings
from app.main import app
from app.models import Base
from app.models.review_dispatch_outbox import ReviewDispatchOutbox
from app.services.webhook_service import GitHubWebhookService, WebhookHandlingResult

SECRET = "test-webhook-secret"


def _signature(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_webhook_verifies_signature_and_accepts_durable_dispatch(
    monkeypatch,
) -> None:
    body = b'{"action":"opened"}'
    task_id = uuid4()
    service = Mock()
    service.handle.return_value = WebhookHandlingResult(
        status="accepted",
        delivery_id="delivery-1",
        review_task_id=task_id,
    )
    monkeypatch.setattr(webhooks, "get_settings", lambda: Settings(github_webhook_secret=SECRET))
    app.dependency_overrides[get_github_webhook_service] = lambda: service

    try:
        response = TestClient(app).post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-1",
                "X-Hub-Signature-256": _signature(body),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "delivery_id": "delivery-1",
        "review_task_id": str(task_id),
    }
    service.handle.assert_called_once_with("delivery-1", "pull_request", body)


def test_github_webhook_accepts_task_without_accessing_broker(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    body = json.dumps(
        {
            "action": "opened",
            "number": 17,
            "pull_request": {
                "number": 17,
                "base": {"sha": "123456abcdef"},
                "head": {"sha": "abcdef123456"},
            },
            "repository": {"id": 42, "full_name": "owner/repository"},
            "installation": {"id": 99},
        }
    ).encode()
    monkeypatch.setattr(webhooks, "get_settings", lambda: Settings(github_webhook_secret=SECRET))
    with factory() as session:
        app.dependency_overrides[get_github_webhook_service] = lambda: GitHubWebhookService(session)
        try:
            response = TestClient(app).post(
                "/api/v1/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-1",
                    "X-Hub-Signature-256": _signature(body),
                },
            )
            assert response.status_code == 202
            assert session.scalar(select(func.count()).select_from(ReviewDispatchOutbox)) == 1
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def test_github_webhook_rejects_invalid_signature(monkeypatch) -> None:
    service = Mock()
    monkeypatch.setattr(webhooks, "get_settings", lambda: Settings(github_webhook_secret=SECRET))
    app.dependency_overrides[get_github_webhook_service] = lambda: service

    try:
        response = TestClient(app).post(
            "/api/v1/webhooks/github",
            content=b"{}",
            headers={
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "delivery-1",
                "X-Hub-Signature-256": "sha256=" + ("0" * 64),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_webhook_signature"
    service.handle.assert_not_called()
