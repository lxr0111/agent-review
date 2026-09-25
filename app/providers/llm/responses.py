from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.providers.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMOutputError,
    LLMPermissionError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTransientError,
)
from app.providers.llm.protocol import LLMReviewResult, LLMTokenUsage, ReviewPrompt
from app.providers.llm.schemas import LLMReviewOutput

_SCHEMA_KEYWORDS_REMOVED_FOR_COMPATIBILITY = frozenset(
    {
        "default",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "title",
    }
)


class _ResponsesContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None
    refusal: str | None = None


class _ResponsesOutputItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    content: tuple[_ResponsesContent, ...] = ()


class _ResponsesUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


class _ResponsesEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    output: tuple[_ResponsesOutputItem, ...]
    usage: _ResponsesUsage | None = None


class ResponsesAPIProvider:
    """Shared Responses API transport with provider-specific compatibility flags."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: SecretStr,
        model: str,
        max_output_tokens: int,
        provider_name: str,
        strict_json_schema: bool,
        store_responses: bool | None,
        owned_http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise LLMConfigurationError("LLM_API_KEY is empty")
        if not model.strip():
            raise LLMConfigurationError("LLM_MODEL is empty")
        if max_output_tokens < 1:
            raise LLMConfigurationError("LLM_MAX_OUTPUT_TOKENS must be positive")
        if not provider_name.strip():
            raise LLMConfigurationError("LLM provider name is empty")

        self._client = client
        self._api_key = SecretStr(api_key.get_secret_value().strip())
        self._model = model.strip()
        self._max_output_tokens = max_output_tokens
        self._provider_name = provider_name.strip()
        self._strict_json_schema = strict_json_schema
        self._store_responses = store_responses
        self._owned_http_client = owned_http_client

    @property
    def model_name(self) -> str:
        return self._model

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "User-Agent": "AgentReview/0.1.0",
        }
        output_format: dict[str, object] = {
            "type": "json_schema",
            "name": "code_review",
            "schema": _review_schema(),
        }
        if self._strict_json_schema:
            output_format["strict"] = True

        body: dict[str, object] = {
            "model": self._model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": prompt.system}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt.user}],
                },
            ],
            "text": {"format": output_format},
            "max_output_tokens": self._max_output_tokens,
        }
        if self._store_responses is not None:
            body["store"] = self._store_responses

        try:
            response = self._client.post("responses", headers=headers, json=body)
        except httpx.TimeoutException:
            raise LLMTransientError(f"{self._provider_name} API request timed out") from None
        except httpx.TransportError:
            raise LLMTransientError(f"{self._provider_name} API connection failed") from None

        if response.status_code != 200:
            _raise_for_status(response, self._provider_name)
            raise LLMResponseError(
                f"{self._provider_name} API returned an unexpected success status",
                status_code=response.status_code,
                request_id=_request_id(response),
            )
        return _parse_review_output(response, self._provider_name)

    def close(self) -> None:
        if self._owned_http_client is not None:
            self._owned_http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _parse_review_output(response: httpx.Response, provider_name: str) -> LLMReviewResult:
    status_code = response.status_code
    request_id = _request_id(response)
    try:
        envelope = _ResponsesEnvelope.model_validate(response.json())
    except (ValueError, ValidationError):
        raise LLMOutputError(
            f"{provider_name} response envelope is invalid",
            status_code=status_code,
            request_id=request_id,
        ) from None

    for item in envelope.output:
        for content in item.content:
            if content.type == "refusal":
                raise LLMRefusalError(
                    f"{provider_name} refused the review request",
                    status_code=status_code,
                    request_id=request_id,
                )

    if envelope.status != "completed":
        raise LLMOutputError(
            f"{provider_name} response did not complete",
            status_code=status_code,
            request_id=request_id,
        )

    output_texts = [
        content.text
        for item in envelope.output
        if item.type == "message"
        for content in item.content
        if content.type == "output_text" and content.text
    ]
    if len(output_texts) != 1:
        raise LLMOutputError(
            f"{provider_name} response must contain exactly one structured output",
            status_code=status_code,
            request_id=request_id,
        )
    try:
        output = LLMReviewOutput.model_validate_json(output_texts[0])
    except (ValueError, ValidationError):
        raise LLMOutputError(
            f"{provider_name} structured review output is invalid",
            status_code=status_code,
            request_id=request_id,
        ) from None
    usage = None
    if envelope.usage is not None:
        usage = LLMTokenUsage(
            input_tokens=envelope.usage.input_tokens,
            output_tokens=envelope.usage.output_tokens,
            total_tokens=envelope.usage.total_tokens,
        )
    return LLMReviewResult(output=output, usage=usage)


def _raise_for_status(response: httpx.Response, provider_name: str) -> None:
    status_code = response.status_code
    request_id = _request_id(response)

    if status_code == 401:
        raise LLMAuthenticationError(
            f"{provider_name} API authentication failed",
            status_code=status_code,
            request_id=request_id,
        )
    if status_code == 403:
        raise LLMPermissionError(
            f"{provider_name} API permission was denied",
            status_code=status_code,
            request_id=request_id,
        )
    if status_code == 429:
        raise LLMRateLimitError(
            f"{provider_name} API rate limit was exceeded",
            status_code=status_code,
            request_id=request_id,
            retry_after_seconds=_parse_retry_after(response.headers.get("retry-after")),
        )
    if status_code in {408, 409} or 500 <= status_code:
        raise LLMTransientError(
            f"{provider_name} API is temporarily unavailable",
            status_code=status_code,
            request_id=request_id,
        )
    if 400 <= status_code:
        raise LLMResponseError(
            f"{provider_name} API request was rejected",
            status_code=status_code,
            request_id=request_id,
        )


def _request_id(response: httpx.Response) -> str | None:
    request_id = response.headers.get("x-request-id")
    return request_id if isinstance(request_id, str) else None


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _review_schema() -> dict[str, object]:
    sanitized = _remove_unsupported_schema_keywords(LLMReviewOutput.model_json_schema())
    if not isinstance(sanitized, dict):
        raise LLMConfigurationError("Review output schema must be a JSON object")
    return sanitized


def _remove_unsupported_schema_keywords(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _remove_unsupported_schema_keywords(item)
            for key, item in value.items()
            if key not in _SCHEMA_KEYWORDS_REMOVED_FOR_COMPATIBILITY
        }
    if isinstance(value, list):
        return [_remove_unsupported_schema_keywords(item) for item in value]
    return value
