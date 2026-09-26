from dataclasses import dataclass
from typing import ClassVar

from app.domain.review.entities import ReviewContext
from app.services.review_agent import AgentRole


@dataclass(frozen=True, slots=True)
class PlannedAgent:
    role: AgentRole
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    agents: tuple[PlannedAgent, ...]

    @property
    def roles(self) -> tuple[AgentRole, ...]:
        return tuple(agent.role for agent in self.agents)


class DeterministicReviewPlanner:
    """Selects specialist roles using auditable text signals, without an LLM call."""

    _ROLE_ORDER: ClassVar[tuple[AgentRole, ...]] = (
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
        AgentRole.PERFORMANCE,
        AgentRole.MAINTAINABILITY,
    )
    _SIGNALS: ClassVar[
        dict[AgentRole, tuple[tuple[str, tuple[str, ...]], ...]]
    ] = {
        AgentRole.SECURITY: (
            ("database input boundary", ("cursor.execute", "select *", "sql", "raw query")),
            ("command execution boundary", ("subprocess", "shell=true", "os.system", "eval(")),
            ("authorization boundary", ("authorization", "permission", "current_user", "admin")),
            ("path or upload boundary", ("upload", "path traversal", "read_bytes", "write_bytes")),
            ("credential boundary", ("password", "secret", "access token", "api key")),
        ),
        AgentRole.PERFORMANCE: (
            ("repeated data access", ("db.query", "db.get(", "n+1", "n + 1")),
            ("blocking asynchronous work", ("time.sleep", "blocking", "event loop")),
            ("repeated full collection work", ("sorted(", ".count(", ".index(")),
            ("complexity concern", ("quadratic", "o(n", "nested loop")),
        ),
        AgentRole.MAINTAINABILITY: (
            (
                "duplicated rule signal",
                (
                    "duplicate business rule",
                    "duplicated business rule",
                    "duplicated logic",
                    "copy-paste",
                    "copy paste",
                    "same rule",
                ),
            ),
            ("canonicalization rule signal", ("normalize", "normalization", "canonical")),
            ("single source of truth signal", ("single source", "centralize", "business rule")),
        ),
    }

    def plan(self, context: ReviewContext) -> ReviewPlan:
        corpus = self._corpus(context)
        reasons: dict[AgentRole, list[str]] = {
            AgentRole.CORRECTNESS: [
                "baseline correctness and reliability review for every code change"
            ]
        }
        for role, rules in self._SIGNALS.items():
            matched_reasons = [
                reason
                for reason, signals in rules
                if any(signal in corpus for signal in signals)
            ]
            if matched_reasons:
                reasons[role] = matched_reasons

        if self._has_repeated_added_logic(context):
            reasons.setdefault(AgentRole.MAINTAINABILITY, []).append(
                "the same executable line is added at least three times"
            )

        return ReviewPlan(
            agents=tuple(
                PlannedAgent(role=role, reasons=tuple(reasons[role]))
                for role in self._ROLE_ORDER
                if role in reasons
            )
        )

    @staticmethod
    def _corpus(context: ReviewContext) -> str:
        pull_request = context.pull_request
        parts = [pull_request.title, pull_request.body or ""]
        for changed_file in context.reviewable_files:
            parts.extend((changed_file.path, changed_file.patch or ""))
        return "\n".join(parts).casefold()

    @staticmethod
    def _has_repeated_added_logic(context: ReviewContext) -> bool:
        added_line_counts: dict[str, int] = {}
        for changed_file in context.reviewable_files:
            for raw_line in (changed_file.patch or "").splitlines():
                if not raw_line.startswith("+") or raw_line.startswith("+++"):
                    continue
                line = raw_line[1:].strip().casefold()
                if (
                    not line
                    or line.startswith(("#", "def ", "class ", "import ", "from "))
                ):
                    continue
                added_line_counts[line] = added_line_counts.get(line, 0) + 1
        return any(count >= 3 for count in added_line_counts.values())
