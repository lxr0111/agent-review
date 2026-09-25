import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.llm.exceptions import LLMConfigurationError
from app.providers.llm.responses import ResponsesAPIProvider

DEEPSEEK_API_BASE_URL = "https://api.deepseek.com/"


class DeepSeekResponsesProvider(ResponsesAPIProvider):
    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: SecretStr,
        model: str,
        max_output_tokens: int,
        owned_http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            client=client,
            api_key=api_key,
            model=model,
            max_output_tokens=max_output_tokens,
            provider_name="DeepSeek",
            strict_json_schema=False,
            store_responses=None,
            owned_http_client=owned_http_client,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> "DeepSeekResponsesProvider":
        if settings.llm_api_key is None:
            raise LLMConfigurationError("LLM_API_KEY is not configured")
        if settings.llm_model is None:
            raise LLMConfigurationError("LLM_MODEL is not configured")

        owned_http_client = None
        if http_client is None:
            owned_http_client = httpx.Client(
                base_url=settings.llm_base_url or DEEPSEEK_API_BASE_URL,
                timeout=settings.llm_timeout_seconds,
                follow_redirects=False,
            )
            http_client = owned_http_client
        try:
            return cls(
                client=http_client,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                max_output_tokens=settings.llm_max_output_tokens,
                owned_http_client=owned_http_client,
            )
        except Exception:
            if owned_http_client is not None:
                owned_http_client.close()
            raise
