from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.providers.llm.schemas import LLMReviewOutput


@dataclass(frozen=True, slots=True)
class ReviewPrompt:
    system: str
    user: str
    version: str


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewOutput: ...
