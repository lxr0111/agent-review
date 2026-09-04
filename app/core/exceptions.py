from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ApplicationError(Exception):
    error_code = "application_error"
    status_code = status.HTTP_400_BAD_REQUEST


class ReviewTaskNotFound(ApplicationError):
    error_code = "review_task_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class InvalidStateTransition(ApplicationError):
    error_code = "invalid_state_transition"
    status_code = status.HTTP_409_CONFLICT


class InvalidWebhookSignature(ApplicationError):
    error_code = "invalid_webhook_signature"
    status_code = status.HTTP_401_UNAUTHORIZED


class InvalidWebhookPayload(ApplicationError):
    error_code = "invalid_webhook_payload"
    status_code = status.HTTP_400_BAD_REQUEST


class WebhookNotConfigured(ApplicationError):
    error_code = "webhook_not_configured"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def handle_application_error(_request: Request, exc: ApplicationError) -> JSONResponse:
        content: dict[str, Any] = {"error": exc.error_code, "message": str(exc)}
        return JSONResponse(status_code=exc.status_code, content=content)
