import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.llm import DeepSeekResponsesProvider, LLMProvider, create_llm_provider
from app.providers.llm.exceptions import LLMConfigurationError, LLMResponseError
from app.providers.llm.protocol import ReviewPrompt

API_KEY = "deepseek-test-key-must-not-leak"


def _prompt() -> ReviewPrompt:
    return ReviewPrompt(
        system="Review the supplied pull request.",
        user='{"reviewable_files":[]}',
        version="review-v1",
    )


def _response_payload(*, include_usage: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "resp_deepseek_123",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"summary":"No issue found.","findings":[]}',
                    }
                ],
            }
        ],
    }
    if include_usage:
        payload["usage"] = {
            "input_tokens": 81,
            "input_tokens_details": {"cached_tokens": 16},
            "output_tokens": 19,
            "output_tokens_details": {"reasoning_tokens": 7},
            "total_tokens": 100,
        }
    return payload


def _provider(handler) -> DeepSeekResponsesProvider:
    client = httpx.Client(
        base_url="https://api.deepseek.test/",
        transport=httpx.MockTransport(handler),
    )
    return DeepSeekResponsesProvider(
        client=client,
        api_key=SecretStr(API_KEY),
        model="deepseek-flash",
        max_output_tokens=2_048,
    )


def test_generate_review_uses_deepseek_responses_compatibility() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        output_format = body["text"]["format"]

        assert request.url.path == "/responses"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert body["model"] == "deepseek-flash"
        assert body["max_output_tokens"] == 2_048
        assert body["input"][0]["role"] == "system"
        assert body["input"][1]["role"] == "user"
        assert output_format["type"] == "json_schema"
        assert "strict" not in output_format
        assert "store" not in body
        assert set(output_format["schema"]["required"]) == {"summary", "findings"}
        return httpx.Response(
            200,
            json=_response_payload(),
            headers={"X-Request-Id": "deepseek-request-123"},
            request=request,
        )

    provider = _provider(handler)

    result = provider.generate_review(_prompt())

    assert isinstance(provider, LLMProvider)
    assert result.output.summary == "No issue found."
    assert result.output.findings == ()
    assert result.usage is not None
    assert result.usage.input_tokens == 81
    assert result.usage.output_tokens == 19
    assert result.usage.total_tokens == 100
    assert provider.model_name == "deepseek-flash"


def test_generate_review_allows_missing_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response_payload(include_usage=False),
            request=request,
        )

    result = _provider(handler).generate_review(_prompt())

    assert result.output.summary == "No issue found."
    assert result.usage is None


def test_generate_review_uses_provider_name_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"error": {"message": API_KEY}},
            request=request,
        )

    with pytest.raises(LLMResponseError, match="DeepSeek API request was rejected") as caught:
        _provider(handler).generate_review(_prompt())

    assert caught.value.retryable is False
    assert API_KEY not in str(caught.value)


def test_deepseek_provider_requires_generic_credentials_and_model() -> None:
    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
        DeepSeekResponsesProvider.from_settings(
            Settings(
                llm_provider="deepseek",
                llm_model="deepseek-flash",
                llm_api_key=None,
            )
        )

    with pytest.raises(LLMConfigurationError, match="LLM_MODEL"):
        DeepSeekResponsesProvider.from_settings(
            Settings(
                llm_provider="deepseek",
                llm_model=None,
                llm_api_key=SecretStr(API_KEY),
            )
        )


def test_deepseek_provider_factory_accepts_custom_base_url() -> None:
    provider = create_llm_provider(
        Settings(
            llm_provider="deepseek",
            llm_base_url="https://gateway.example/deepseek/v1/",
            llm_model="deepseek-flash",
            llm_api_key=SecretStr(API_KEY),
        )
    )

    assert isinstance(provider, DeepSeekResponsesProvider)
    assert str(provider._client.base_url) == "https://gateway.example/deepseek/v1/"
    assert API_KEY not in repr(provider.__dict__)
    provider.close()
