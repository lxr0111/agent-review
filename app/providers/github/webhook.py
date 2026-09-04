import json
from typing import Any

from app.core.exceptions import InvalidWebhookPayload


def parse_webhook_json(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebhookPayload("GitHub webhook body is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise InvalidWebhookPayload("GitHub webhook body must be a JSON object")
    return payload


def get_webhook_action(payload: dict[str, Any]) -> str | None:
    action = payload.get("action")
    return action if isinstance(action, str) else None
