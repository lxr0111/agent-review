import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.providers.github.auth import GitHubAppAuth
from app.providers.github.exceptions import (
    GitHubConfigurationError,
    GitHubPayloadError,
)
from app.providers.github.transport import GitHubHttpTransport

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


def _key_pair() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _transport(handler=None) -> GitHubHttpTransport:
    if handler is None:
        handler = lambda request: httpx.Response(500, request=request)
    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    return GitHubHttpTransport(client, "2026-03-10")


def test_create_app_jwt_uses_rs256_and_expected_claims() -> None:
    private_pem, public_pem = _key_pair()
    auth = GitHubAppAuth(
        client_id="Iv1.test-client",
        private_key=private_pem,
        transport=_transport(),
        clock=lambda: NOW,
    )

    encoded = auth.create_app_jwt()
    claims = jwt.decode(
        encoded,
        public_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )

    assert jwt.get_unverified_header(encoded)["alg"] == "RS256"
    assert claims == {
        "iat": int((NOW - timedelta(seconds=60)).timestamp()),
        "exp": int((NOW + timedelta(minutes=9)).timestamp()),
        "iss": "Iv1.test-client",
    }


def test_installation_token_is_requested_once_and_cached() -> None:
    private_pem, _ = _key_pair()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/app/installations/42/access_tokens"
        assert request.headers["authorization"].startswith("Bearer ey")
        assert request.headers["x-github-api-version"] == "2026-03-10"
        assert json.loads(request.content) == {
            "permissions": {"contents": "read", "pull_requests": "read"}
        }
        return httpx.Response(
            201,
            json={"token": "ghs_secret-token", "expires_at": "2026-09-04T09:00:00Z"},
            request=request,
        )

    auth = GitHubAppAuth(
        client_id="Iv1.test-client",
        private_key=private_pem,
        transport=_transport(handler),
        clock=lambda: NOW,
    )

    first = auth.get_installation_token(42)
    second = auth.get_installation_token(42)

    assert first is second
    assert first.value.get_secret_value() == "ghs_secret-token"
    assert "ghs_secret-token" not in repr(first)
    assert len(requests) == 1


def test_installation_token_refreshes_before_expiry() -> None:
    private_pem, _ = _key_pair()
    current_time = [NOW]
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        expires_at = current_time[0] + timedelta(hours=1)
        return httpx.Response(
            201,
            json={
                "token": f"ghs_token-{request_count}",
                "expires_at": expires_at.isoformat(),
            },
            request=request,
        )

    auth = GitHubAppAuth(
        client_id="Iv1.test-client",
        private_key=private_pem,
        transport=_transport(handler),
        refresh_buffer=timedelta(minutes=5),
        clock=lambda: current_time[0],
    )

    first = auth.get_installation_token(42)
    current_time[0] += timedelta(minutes=56)
    second = auth.get_installation_token(42)

    assert first.value.get_secret_value() == "ghs_token-1"
    assert second.value.get_secret_value() == "ghs_token-2"
    assert request_count == 2


def test_invalid_private_key_is_reported_as_configuration_error() -> None:
    auth = GitHubAppAuth(
        client_id="Iv1.test-client",
        private_key=b"not-a-private-key",
        transport=_transport(),
        clock=lambda: NOW,
    )

    with pytest.raises(GitHubConfigurationError, match="private key is invalid"):
        auth.create_app_jwt()


def test_missing_private_key_file_does_not_expose_path() -> None:
    with pytest.raises(GitHubConfigurationError, match="could not be read") as caught:
        GitHubAppAuth.from_private_key_file(
            client_id="Iv1.test-client",
            private_key_path=Path("secrets/private-production-key.pem"),
            transport=_transport(),
        )

    assert "private-production-key" not in str(caught.value)


def test_expired_installation_token_response_is_rejected() -> None:
    private_pem, _ = _key_pair()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"token": "ghs_expired", "expires_at": "2026-09-04T07:59:59Z"},
            request=request,
        )

    auth = GitHubAppAuth(
        client_id="Iv1.test-client",
        private_key=private_pem,
        transport=_transport(handler),
        clock=lambda: NOW,
    )

    with pytest.raises(GitHubPayloadError, match="already expired"):
        auth.get_installation_token(42)
