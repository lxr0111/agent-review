class LLMProviderError(Exception):
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


class LLMConfigurationError(LLMProviderError):
    pass


class LLMAuthenticationError(LLMProviderError):
    pass


class LLMPermissionError(LLMProviderError):
    pass


class LLMRateLimitError(LLMProviderError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None,
        retry_after_seconds: int | None,
    ) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id)
        self.retry_after_seconds = retry_after_seconds


class LLMTransientError(LLMProviderError):
    retryable = True


class LLMResponseError(LLMProviderError):
    pass


class LLMOutputError(LLMProviderError):
    pass


class LLMRefusalError(LLMProviderError):
    pass
