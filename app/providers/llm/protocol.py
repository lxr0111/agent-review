from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.providers.llm.schemas import LLMReviewOutput


@dataclass(frozen=True, slots=True)
class ReviewPrompt:
    system: str
    user: str
    version: str


@dataclass(frozen=True, slots=True)
class LLMTokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0 or self.total_tokens < 0:
            raise ValueError("LLM token usage values must not be negative")


@dataclass(frozen=True, slots=True)
class LLMReviewResult:
    output: LLMReviewOutput
    usage: LLMTokenUsage | None = None


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult: ...
