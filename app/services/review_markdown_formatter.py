import html
from uuid import UUID

from app.domain.review.report import FindingSeverity, ReviewFinding, ReviewReport

_SEVERITY_ORDER = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
}
_SEVERITY_ICON = {
    FindingSeverity.CRITICAL: "🔴",
    FindingSeverity.HIGH: "🟠",
    FindingSeverity.MEDIUM: "🟡",
    FindingSeverity.LOW: "🔵",
}


class ReviewMarkdownFormatter:
    def __init__(self, max_characters: int = 60_000) -> None:
        if max_characters < 1_000:
            raise ValueError("Review Markdown limit must be at least 1000 characters")
        self._max_characters = max_characters

    def format(self, report: ReviewReport, *, task_id: UUID, report_digest: str) -> str:
        marker = review_marker(task_id, report_digest)
        lines = [
            "## 🤖 AgentReview summary",
            "",
            f"**Overall risk:** `{report.risk_level.value.upper()}`",
            "",
            _safe_text(report.summary),
            "",
            (
                f"Reviewed **{report.reviewed_file_count}** file(s); "
                f"skipped **{report.skipped_file_count}** file(s)."
            ),
            "",
            "### Findings",
            "",
        ]

        indexed_findings = list(enumerate(report.findings))
        indexed_findings.sort(key=lambda item: (_SEVERITY_ORDER[item[1].severity], item[0]))
        if not indexed_findings:
            lines.extend(["✅ No actionable findings were identified.", ""])
        else:
            for position, (_, finding) in enumerate(indexed_findings, start=1):
                lines.extend(_format_finding(finding, position))

        lines.extend(
            [
                "<details>",
                "<summary>Review metadata</summary>",
                "",
                f"- Model: `{_safe_inline(report.model_name or 'system')}`",
                f"- Prompt version: `{_safe_inline(report.prompt_version)}`",
                "",
                "</details>",
            ]
        )
        content = "\n".join(lines).rstrip()
        suffix = f"\n\n{marker}"
        if len(content) + len(suffix) <= self._max_characters:
            return content + suffix

        truncation = "\n\n> ⚠️ Report truncated because it exceeded the configured limit."
        available = self._max_characters - len(truncation) - len(suffix)
        return content[:available].rstrip() + truncation + suffix


def review_marker(task_id: UUID, report_digest: str) -> str:
    if len(report_digest) != 64 or any(
        character not in "0123456789abcdef" for character in report_digest
    ):
        raise ValueError("Review report digest must be a lowercase SHA-256 hex digest")
    return f"<!-- agent-review:task={task_id}:report={report_digest} -->"


def _format_finding(finding: ReviewFinding, position: int) -> list[str]:
    icon = _SEVERITY_ICON[finding.severity]
    return [
        f"#### {position}. {icon} [{finding.severity.value.upper()}] {_safe_inline(finding.title)}",
        "",
        f"- **Category:** `{finding.category.value}`",
        f"- **File:** <code>{html.escape(finding.file_path, quote=False)}</code>",
        f"- **Confidence:** {finding.confidence:.0%}",
        "",
        "**Why it matters**",
        "",
        _safe_text(finding.explanation),
        "",
        "**Suggested change**",
        "",
        _safe_text(finding.suggestion),
        "",
    ]


def _safe_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_safe_inline(line) for line in normalized.split("\n"))


def _safe_inline(value: str) -> str:
    single_line = value.replace("\r", " ").replace("\n", " ")
    escaped = html.escape(single_line, quote=False).replace("@", "@\u200b")
    for character in ("\\", "`", "*", "_", "[", "]", "|", "#", ">"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
