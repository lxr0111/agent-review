import hashlib
import hmac
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import get_github_webhook_service
from app.api.routes import webhooks
from app.core.config import Settings
from app.main import app
from app.services.webhook_service import WebhookHandlingResult

SECRET = "test-webhook-secret"


def _signature(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_webhook_verifies_signature_and_enqueues_after_acceptance(
    monkeypatch,
) -> None:
    body = b'{"action":"opened"}'
    task_id = uuid4()
    service = Mock()
    service.handle.return_value = WebhookHandlingResult(
        status="accepted",
        delivery_id="delivery-1",
        review_task_id=task_id,
        should_enqueue=True,
    )
    delay = Mock()
    monkeypatch.setattr(webhooks, "get_settings", lambda: Settings(github_webhook_secret=SECRET))
    monkeypatch.setattr(webhooks.run_review, "delay", delay)
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
    delay.assert_called_once_with(str(task_id))


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
