from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.review.report import FindingCategory, FindingSeverity


class LLMReviewLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class LLMReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=2_000)
    suggestion: str = Field(min_length=1, max_length=2_000)
    file_path: str = Field(min_length=1, max_length=1_024)
    severity: FindingSeverity
    category: FindingCategory
    confidence: float = Field(ge=0.0, le=1.0)
    location: LLMReviewLocation | None = None


class LLMReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=4_000)
    findings: tuple[LLMReviewFinding, ...] = Field(max_length=50)
