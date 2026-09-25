from app.core.config import Settings
from app.providers.llm.deepseek import DeepSeekResponsesProvider
from app.providers.llm.exceptions import LLMConfigurationError
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.openai import OpenAIResponsesProvider
from app.providers.llm.protocol import LLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    if settings.llm_provider == "openai":
        return OpenAIResponsesProvider.from_settings(settings)
    if settings.llm_provider == "deepseek":
        return DeepSeekResponsesProvider.from_settings(settings)
    raise LLMConfigurationError(f"Unsupported LLM provider: {settings.llm_provider}")
