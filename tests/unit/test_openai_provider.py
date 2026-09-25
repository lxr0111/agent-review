import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.review.report import FindingCategory, FindingSeverity
from app.providers.llm import LLMProvider, MockLLMProvider, create_llm_provider
from app.providers.llm.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMOutputError,
    LLMPermissionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponseError,
    LLMTransientError,
)
from app.providers.llm.openai import OpenAIResponsesProvider
from app.providers.llm.protocol import ReviewPrompt
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput

API_KEY = "sk-test-key-must-not-leak"


def _prompt() -> ReviewPrompt:
    return ReviewPrompt(
        system="Review the supplied pull request.",
        user='{"reviewable_files":[]}',
        version="review-v1",
    )


def _structured_output() -> LLMReviewOutput:
    return LLMReviewOutput(
        summary="One correctness problem was found.",
        findings=(
            LLMReviewFinding(
                title="Missing empty-input guard",
                explanation="The new branch indexes an empty collection.",
                suggestion="Return early when the collection is empty.",
                file_path="src/service.py",
                severity=FindingSeverity.HIGH,
                category=FindingCategory.CORRECTNESS,
                confidence=0.94,
            ),
        ),
    )


def _response_payload(
    *,
    output_text: str | None = None,
    status: str = "completed",
    include_usage: bool = True,
) -> dict[str, object]:
    text = output_text if output_text is not None else _structured_output().model_dump_json()
    payload: dict[str, object] = {
        "id": "resp_123",
        "status": status,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ],
    }
    if include_usage:
        payload["usage"] = {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 35,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 155,
        }
    return payload


def _provider(handler) -> OpenAIResponsesProvider:
    client = httpx.Client(
        base_url="https://api.openai.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    return OpenAIResponsesProvider(
        client=client,
        api_key=SecretStr(API_KEY),
        model="test-structured-model",
        max_output_tokens=2_048,
    )


def test_generate_review_uses_responses_api_json_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = body["text"]["format"]["schema"]

        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert body["model"] == "test-structured-model"
        assert body["store"] is False
        assert body["max_output_tokens"] == 2_048
        assert body["input"][0]["role"] == "system"
        assert body["input"][1]["role"] == "user"
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert set(schema["required"]) == {"summary", "findings"}
        assert schema["additionalProperties"] is False
        finding_schema = schema["$defs"]["LLMReviewFinding"]
        assert finding_schema["additionalProperties"] is False
        assert set(finding_schema["required"]) == {
            "title",
            "explanation",
            "suggestion",
            "file_path",
            "severity",
            "category",
            "confidence",
        }
        serialized_schema = json.dumps(schema)
        for removed_keyword in (
            "maxItems",
            "maxLength",
            "maximum",
            "minLength",
            "minimum",
            "title",
        ):
            assert f'"{removed_keyword}":' not in serialized_schema
        return httpx.Response(
            200,
            json=_response_payload(),
            headers={"X-Request-Id": "request-123"},
            request=request,
        )

    provider = _provider(handler)

    result = provider.generate_review(_prompt())

    assert isinstance(provider, LLMProvider)
    assert result.output == _structured_output()
    assert result.usage is not None
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 35
    assert result.usage.total_tokens == 155
    assert provider.model_name == "test-structured-model"


def test_generate_review_allows_missing_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_payload(include_usage=False),
            request=request,
        )

    result = _provider(handler).generate_review(_prompt())

    assert result.output == _structured_output()
    assert result.usage is None


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (400, LLMResponseError),
        (408, LLMTransientError),
        (409, LLMTransientError),
        (401, LLMAuthenticationError),
        (403, LLMPermissionError),
        (500, LLMTransientError),
        (503, LLMTransientError),
    ],
)
def test_generate_review_classifies_http_errors(
    status_code: int,
    expected_error: type[LLMProviderError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"message": API_KEY}},
            headers={"X-Request-Id": "request-123"},
            request=request,
        )

    with pytest.raises(expected_error) as caught:
        _provider(handler).generate_review(_prompt())

    assert API_KEY not in str(caught.value)
    assert caught.value.request_id == "request-123"


def test_generate_review_preserves_rate_limit_retry_delay() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "30", "X-Request-Id": "request-123"},
            request=request,
        )

    with pytest.raises(LLMRateLimitError) as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 30
    assert caught.value.request_id == "request-123"


def test_generate_review_converts_timeout_to_retryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"transport contained {API_KEY}", request=request)

    with pytest.raises(LLMTransientError) as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.retryable is True
    assert str(caught.value) == "OpenAI API request timed out"
    assert API_KEY not in str(caught.value)
    assert caught.value.__cause__ is None


def test_generate_review_converts_connection_failure_to_retryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection details must not leak", request=request)

    with pytest.raises(LLMTransientError, match="connection failed") as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.retryable is True
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "output_text",
    [
        "not-json",
        '{"summary":"Missing findings"}',
        (
            '{"summary":"Bad category","findings":[{"title":"Issue",'
            '"explanation":"Details","suggestion":"Fix it","file_path":"src/a.py",'
            '"severity":"high","category":"formatting","confidence":0.9}]}'
        ),
    ],
)
def test_generate_review_rejects_invalid_structured_output(output_text: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_payload(output_text=output_text),
            headers={"X-Request-Id": "request-123"},
            request=request,
        )

    with pytest.raises(LLMOutputError) as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.request_id == "request-123"


def test_generate_review_rejects_incomplete_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_payload(status="incomplete"),
            request=request,
        )

    with pytest.raises(LLMOutputError, match="did not complete"):
        _provider(handler).generate_review(_prompt())


def test_generate_review_rejects_invalid_response_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="not-json",
            headers={"X-Request-Id": "request-123"},
            request=request,
        )

    with pytest.raises(LLMOutputError, match="envelope") as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.request_id == "request-123"
    assert caught.value.__cause__ is None


def test_generate_review_reports_model_refusal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "refusal",
                                "refusal": "Repository content cannot be reviewed.",
                            }
                        ],
                    }
                ],
            },
            request=request,
        )

    with pytest.raises(LLMRefusalError, match="refused"):
        _provider(handler).generate_review(_prompt())


def test_provider_requires_openai_credentials_and_model() -> None:
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        OpenAIResponsesProvider.from_settings(
            Settings(
                _env_file=None,
                llm_provider="openai",
                llm_model="test-model",
                llm_api_key=None,
                openai_api_key=None,
            )
        )

    with pytest.raises(LLMConfigurationError, match="LLM_MODEL"):
        OpenAIResponsesProvider.from_settings(
            Settings(
                _env_file=None,
                llm_provider="openai",
                llm_model=None,
                llm_api_key=None,
                openai_api_key=SecretStr(API_KEY),
            )
        )


def test_provider_factory_uses_mock_by_default() -> None:
    provider = create_llm_provider(Settings(llm_provider="mock"))

    assert isinstance(provider, MockLLMProvider)


def test_provider_factory_builds_openai_provider_without_calling_api() -> None:
    provider = create_llm_provider(
        Settings(
            llm_provider="openai",
            llm_model="test-structured-model",
            openai_api_key=SecretStr(API_KEY),
        )
    )

    assert isinstance(provider, OpenAIResponsesProvider)
    assert API_KEY not in repr(provider.__dict__)
    provider.close()
