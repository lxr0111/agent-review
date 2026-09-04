import hashlib
import hmac

import pytest

from app.core.exceptions import InvalidWebhookSignature
from app.core.security import verify_github_signature


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_signature_accepts_matching_body() -> None:
    body = '{"message":"你好"}'.encode()

    verify_github_signature(body, _signature(body, "test-secret"), "test-secret")


@pytest.mark.parametrize("signature", [None, "", "sha1=abc", "sha256=abc"])
def test_verify_github_signature_rejects_missing_or_malformed_signature(
    signature: str | None,
) -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_github_signature(b"{}", signature, "test-secret")


def test_verify_github_signature_rejects_modified_body() -> None:
    signature = _signature(b'{"action":"opened"}', "test-secret")

    with pytest.raises(InvalidWebhookSignature):
        verify_github_signature(b'{"action":"closed"}', signature, "test-secret")
