from pydantic import BaseModel, ConfigDict, Field

from app.domain.review.report import FindingCategory, FindingSeverity


class LLMReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=2_000)
    suggestion: str = Field(min_length=1, max_length=2_000)
    file_path: str = Field(min_length=1, max_length=1_024)
    severity: FindingSeverity
    category: FindingCategory
    confidence: float = Field(ge=0.0, le=1.0)


class LLMReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=4_000)
    findings: tuple[LLMReviewFinding, ...] = Field(max_length=50)
