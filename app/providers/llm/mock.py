from app.providers.llm.protocol import LLMReviewResult, ReviewPrompt
from app.providers.llm.schemas import LLMReviewOutput


class MockLLMProvider:
    def __init__(
        self,
        output: LLMReviewOutput | None = None,
        *,
        model_name: str = "mock-review-model",
    ) -> None:
        self._output = output or LLMReviewOutput(
            summary="No actionable problems were found in the supplied changes.",
            findings=(),
        )
        self._model_name = model_name
        self.prompts: list[ReviewPrompt] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_review(self, prompt: ReviewPrompt) -> LLMReviewResult:
        self.prompts.append(prompt)
        return LLMReviewResult(output=self._output, usage=None)
