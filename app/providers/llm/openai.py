from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from app.core.config import Settings
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
from app.providers.llm.protocol import ReviewPrompt
from app.providers.llm.schemas import LLMReviewOutput

OPENAI_API_BASE_URL = "https://api.openai.com/v1/"
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


class _OpenAIContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None
    refusal: str | None = None


class _OpenAIOutputItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    content: tuple[_OpenAIContent, ...] = ()


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    output: tuple[_OpenAIOutputItem, ...]


class OpenAIResponsesProvider:
    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: SecretStr,
        model: str,
        max_output_tokens: int,
        owned_http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise LLMConfigurationError("OPENAI_API_KEY is empty")
        if not model.strip():
            raise LLMConfigurationError("LLM_MODEL is empty")
        if max_output_tokens < 1:
            raise LLMConfigurationError("LLM_MAX_OUTPUT_TOKENS must be positive")
        self._client = client
        self._api_key = SecretStr(api_key.get_secret_value().strip())
        self._model = model.strip()
        self._max_output_tokens = max_output_tokens
        self._owned_http_client = owned_http_client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> "OpenAIResponsesProvider":
        if settings.openai_api_key is None:
            raise LLMConfigurationError("OPENAI_API_KEY is not configured")
        if settings.llm_model is None:
            raise LLMConfigurationError("LLM_MODEL is not configured")

        owned_http_client = None
        if http_client is None:
            owned_http_client = httpx.Client(
                base_url=OPENAI_API_BASE_URL,
                timeout=settings.llm_timeout_seconds,
                follow_redirects=False,
            )
            http_client = owned_http_client
        try:
            return cls(
                client=http_client,
                api_key=settings.openai_api_key,
                model=settings.llm_model,
                max_output_tokens=settings.llm_max_output_tokens,
                owned_http_client=owned_http_client,
            )
        except Exception:
            if owned_http_client is not None:
                owned_http_client.close()
            raise

    @property
    def model_name(self) -> str:
        return self._model

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewOutput:
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "User-Agent": "AgentReview/0.1.0",
        }
        body = {
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
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "code_review",
                    "strict": True,
                    "schema": _openai_review_schema(),
                }
            },
            "max_output_tokens": self._max_output_tokens,
            "store": False,
        }
        try:
            response = self._client.post("responses", headers=headers, json=body)
        except httpx.TimeoutException:
            raise LLMTransientError("OpenAI API request timed out") from None
        except httpx.TransportError:
            raise LLMTransientError("OpenAI API connection failed") from None

        if response.status_code != 200:
            _raise_for_status(response)
            raise LLMResponseError(
                "OpenAI API returned an unexpected success status",
                status_code=response.status_code,
                request_id=_request_id(response),
            )
        return _parse_review_output(response)

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


def _parse_review_output(response: httpx.Response) -> LLMReviewOutput:
    status_code = response.status_code
    request_id = _request_id(response)
    try:
        envelope = _OpenAIResponse.model_validate(response.json())
    except (ValueError, ValidationError):
        raise LLMOutputError(
            "OpenAI response envelope is invalid",
            status_code=status_code,
            request_id=request_id,
        ) from None

    for item in envelope.output:
        for content in item.content:
            if content.type == "refusal":
                raise LLMRefusalError(
                    "OpenAI refused the review request",
                    status_code=status_code,
                    request_id=request_id,
                )

    if envelope.status != "completed":
        raise LLMOutputError(
            "OpenAI response did not complete",
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
            "OpenAI response must contain exactly one structured output",
            status_code=status_code,
            request_id=request_id,
        )
    try:
        return LLMReviewOutput.model_validate_json(output_texts[0])
    except (ValueError, ValidationError):
        raise LLMOutputError(
            "OpenAI structured review output is invalid",
            status_code=status_code,
            request_id=request_id,
        ) from None


def _raise_for_status(response: httpx.Response) -> None:
    status_code = response.status_code
    request_id = _request_id(response)

    if status_code == 401:
        raise LLMAuthenticationError(
            "OpenAI API authentication failed",
            status_code=status_code,
            request_id=request_id,
        )
    if status_code == 403:
        raise LLMPermissionError(
            "OpenAI API permission was denied",
            status_code=status_code,
            request_id=request_id,
        )
    if status_code == 429:
        raise LLMRateLimitError(
            "OpenAI API rate limit was exceeded",
            status_code=status_code,
            request_id=request_id,
            retry_after_seconds=_parse_retry_after(response.headers.get("retry-after")),
        )
    if status_code in {408, 409} or 500 <= status_code:
        raise LLMTransientError(
            "OpenAI API is temporarily unavailable",
            status_code=status_code,
            request_id=request_id,
        )
    if 400 <= status_code:
        raise LLMResponseError(
            "OpenAI API request was rejected",
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


def _openai_review_schema() -> dict[str, object]:
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
