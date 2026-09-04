import hashlib
import hmac

from app.core.exceptions import InvalidWebhookSignature


def verify_github_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not signature or not signature.startswith("sha256="):
        raise InvalidWebhookSignature("Missing or malformed GitHub webhook signature")

    supplied_digest = signature.removeprefix("sha256=")
    if len(supplied_digest) != hashlib.sha256().digest_size * 2:
        raise InvalidWebhookSignature("Malformed GitHub webhook signature")

    expected_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_digest, supplied_digest):
        raise InvalidWebhookSignature("GitHub webhook signature does not match")
