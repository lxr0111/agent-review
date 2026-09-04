from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

import jwt
from jwt.exceptions import InvalidKeyError
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.providers.github.exceptions import (
    GitHubConfigurationError,
    GitHubPayloadError,
)
from app.providers.github.transport import GitHubHttpTransport


class _InstallationTokenResponse(BaseModel):
    token: SecretStr = Field(min_length=1)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InstallationToken:
    value: SecretStr
    expires_at: datetime

    def is_usable(self, now: datetime, refresh_buffer: timedelta) -> bool:
        return self.expires_at > now + refresh_buffer


class GitHubAppAuth:
    def __init__(
        self,
        *,
        client_id: str,
        private_key: bytes,
        transport: GitHubHttpTransport,
        refresh_buffer: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not client_id.strip():
            raise GitHubConfigurationError("GitHub App client ID is not configured")
        if not private_key.strip():
            raise GitHubConfigurationError("GitHub App private key is empty")
        if refresh_buffer < timedelta(0):
            raise GitHubConfigurationError("GitHub token refresh buffer cannot be negative")

        self._client_id = client_id
        self._private_key = private_key
        self._transport = transport
        self._refresh_buffer = refresh_buffer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tokens: dict[int, InstallationToken] = {}
        self._lock = Lock()

    @classmethod
    def from_private_key_file(
        cls,
        *,
        client_id: str,
        private_key_path: Path,
        transport: GitHubHttpTransport,
        refresh_buffer: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
    ) -> "GitHubAppAuth":
        try:
            private_key = private_key_path.read_bytes()
        except OSError as exc:
            raise GitHubConfigurationError("GitHub App private key could not be read") from exc
        return cls(
            client_id=client_id,
            private_key=private_key,
            transport=transport,
            refresh_buffer=refresh_buffer,
            clock=clock,
        )

    def create_app_jwt(self) -> str:
        now = self._now()
        claims = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": self._client_id,
        }
        try:
            return jwt.encode(claims, self._private_key, algorithm="RS256")
        except (InvalidKeyError, ValueError, TypeError) as exc:
            raise GitHubConfigurationError("GitHub App private key is invalid") from exc

    def get_installation_token(self, installation_id: int) -> InstallationToken:
        if installation_id <= 0:
            raise GitHubConfigurationError("GitHub installation ID must be positive")

        with self._lock:
            now = self._now()
            cached = self._tokens.get(installation_id)
            if cached is not None and cached.is_usable(now, self._refresh_buffer):
                return cached

            token = self._request_installation_token(installation_id)
            self._tokens[installation_id] = token
            return token

    def invalidate(self, installation_id: int) -> None:
        with self._lock:
            self._tokens.pop(installation_id, None)

    def _request_installation_token(self, installation_id: int) -> InstallationToken:
        response = self._transport.request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            bearer_token=self.create_app_jwt(),
            expected_status=201,
            json_body={"permissions": {"contents": "read", "pull_requests": "read"}},
        )
        try:
            payload = _InstallationTokenResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise GitHubPayloadError(
                "GitHub installation token response is invalid",
                status_code=response.status_code,
                request_id=response.headers.get("x-github-request-id"),
            ) from exc

        expires_at = payload.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= self._now():
            raise GitHubPayloadError("GitHub returned an already expired installation token")
        return InstallationToken(value=payload.token, expires_at=expires_at)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise GitHubConfigurationError("GitHub authentication clock must be timezone-aware")
        return now.astimezone(UTC)
