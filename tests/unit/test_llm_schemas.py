import pytest
from pydantic import ValidationError

from app.domain.review.report import FindingCategory, FindingSeverity
from app.providers.llm.schemas import LLMReviewFinding, LLMReviewOutput


def _finding_payload() -> dict[str, object]:
    return {
        "title": "Authorization check can be bypassed",
        "explanation": "The new branch accepts requests without checking the caller.",
        "suggestion": "Run the authorization check before entering this branch.",
        "file_path": "src/auth.py",
        "severity": "high",
        "category": "security",
        "confidence": 0.93,
        "location": None,
    }


def test_review_output_parses_a_strict_structured_response() -> None:
    output = LLMReviewOutput.model_validate(
        {
            "summary": "One authorization problem was found.",
            "findings": [_finding_payload()],
        }
    )

    assert output.findings[0].severity == FindingSeverity.HIGH
    assert output.findings[0].category == FindingCategory.SECURITY
    assert output.findings[0].location is None


def test_review_output_parses_line_location() -> None:
    payload = _finding_payload()
    payload["location"] = {"start_line": 12, "end_line": 14}

    finding = LLMReviewFinding.model_validate(payload)

    assert finding.location is not None
    assert finding.location.start_line == 12
    assert finding.location.end_line == 14


@pytest.mark.parametrize(
    "location",
    [
        {"start_line": 0, "end_line": 1},
        {"start_line": 5, "end_line": 4},
        {"start_line": 1},
        {"start_line": 1, "end_line": 1, "side": "RIGHT"},
    ],
)
def test_review_output_rejects_invalid_line_location(location: object) -> None:
    payload = _finding_payload()
    payload["location"] = location

    with pytest.raises(ValidationError):
        LLMReviewFinding.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", "blocker"),
        ("category", "formatting"),
        ("confidence", 1.1),
        ("title", ""),
    ],
)
def test_review_finding_rejects_invalid_values(field: str, value: object) -> None:
    payload = _finding_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        LLMReviewFinding.model_validate(payload)


def test_review_output_rejects_unknown_fields() -> None:
    payload = _finding_payload()
    payload["line_number"] = 12

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LLMReviewFinding.model_validate(payload)


def test_review_output_limits_number_of_findings() -> None:
    finding = LLMReviewFinding.model_validate(_finding_payload())

    with pytest.raises(ValidationError):
        LLMReviewOutput(summary="Too many findings", findings=(finding,) * 51)


def test_review_output_requires_findings_for_strict_json_schema() -> None:
    with pytest.raises(ValidationError):
        LLMReviewOutput.model_validate({"summary": "No problems were found."})
