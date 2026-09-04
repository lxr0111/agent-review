import pytest

from app.core.exceptions import InvalidWebhookPayload
from app.providers.github.webhook import get_webhook_action, parse_webhook_json


def test_parse_webhook_json_returns_object() -> None:
    payload = parse_webhook_json(b'{"action":"opened"}')

    assert payload == {"action": "opened"}
    assert get_webhook_action(payload) == "opened"


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'"text"'])
def test_parse_webhook_json_rejects_invalid_or_non_object_body(body: bytes) -> None:
    with pytest.raises(InvalidWebhookPayload):
        parse_webhook_json(body)


def test_get_webhook_action_ignores_non_string_value() -> None:
    assert get_webhook_action({"action": 123}) is None
