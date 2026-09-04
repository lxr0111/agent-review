from datetime import datetime


class GitHubProviderError(Exception):
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class GitHubConfigurationError(GitHubProviderError):
    pass


class GitHubAuthenticationError(GitHubProviderError):
    pass


class GitHubPermissionError(GitHubProviderError):
    pass


class GitHubNotFoundError(GitHubProviderError):
    pass


class GitHubPayloadError(GitHubProviderError):
    pass


class GitHubFileLimitExceeded(GitHubProviderError):
    pass


class GitHubRateLimitError(GitHubProviderError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None,
        retry_after_seconds: int | None,
        rate_limit_reset_at: datetime | None,
    ) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id)
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit_reset_at = rate_limit_reset_at


class GitHubTransientError(GitHubProviderError):
    retryable = True


class GitHubResponseError(GitHubProviderError):
    pass
