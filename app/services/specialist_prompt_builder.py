from dataclasses import dataclass

from app.domain.review.entities import ReviewContext
from app.providers.llm.prompt_builder import PROMPT_VERSION, ReviewPromptBuilder
from app.providers.llm.protocol import ReviewPrompt
from app.services.review_agent import AgentRole


@dataclass(frozen=True, slots=True)
class SpecialistPromptProfile:
    version: str
    instructions: str


SPECIALIST_PROMPT_PROFILES: dict[AgentRole, SpecialistPromptProfile] = {
    AgentRole.CORRECTNESS: SpecialistPromptProfile(
        version="review-correctness-v2",
        instructions="""Specialist assignment: correctness and reliability.
- Report only concrete correctness or reliability defects.
- Focus on violated behavior contracts, boundary conditions, control flow, error handling,
  resource lifetime, and failure recovery.
- Use the correctness or reliability category that best matches the demonstrated impact.
- Do not report security, performance, maintainability, style, or optional test improvements.""",
    ),
    AgentRole.SECURITY: SpecialistPromptProfile(
        version="review-security-v3",
        instructions="""Specialist assignment: security.
- Report only concrete security defects supported by the supplied change.
- Focus on trust boundaries, injection, authorization, authentication, secrets, unsafe
  deserialization, path handling, and sensitive-data exposure.
- Do not invent attacker control or missing protections that the supplied data does not prove.
- A function name, possible caller, or hypothetical downstream consumer is not evidence of a
  security impact. Require the supplied patch or an explicit pull request contract to show that
  the changed value participates in a security-sensitive decision.
- If the supplied data proves only a display or functional-contract error, omit it instead of
  reclassifying a possible downstream consequence as a security finding.
- Use only the security category and omit correctness, performance, maintainability, and style.""",
    ),
    AgentRole.PERFORMANCE: SpecialistPromptProfile(
        version="review-performance-v2",
        instructions="""Specialist assignment: performance.
- Report only material performance defects directly demonstrated by the supplied change.
- Focus on repeated I/O, N+1 access, blocking work in asynchronous code, avoidable
  superlinear algorithms, and unbounded resource consumption.
- Do not report micro-optimizations without a concrete scaling or latency mechanism.
- Use only the performance category and omit correctness, security, maintainability, and style.""",
    ),
    AgentRole.MAINTAINABILITY: SpecialistPromptProfile(
        version="review-maintainability-v2",
        instructions="""Specialist assignment: objective maintainability.
- Report only objective defects such as duplicated business rules, conflicting sources of truth,
  unreachable code, or changes that demonstrably make future behavior inconsistent.
- Do not report naming, formatting, personal style, optional abstractions, or speculative redesigns.
- Require direct evidence in the changed code and use low severity unless runtime impact is shown.
- Use only the maintainability category.""",
    ),
}


class SpecialistReviewPromptBuilder(ReviewPromptBuilder):
    def __init__(self, role: AgentRole) -> None:
        try:
            self._profile = SPECIALIST_PROMPT_PROFILES[role]
        except KeyError:
            raise ValueError(f"No specialist prompt is defined for role: {role.value}") from None

    @property
    def prompt_version(self) -> str:
        return self._profile.version

    def build(self, context: ReviewContext) -> ReviewPrompt:
        base_prompt = super().build(context)
        return ReviewPrompt(
            system=f"{base_prompt.system}\n\n{self._profile.instructions}",
            user=base_prompt.user,
            version=self.prompt_version,
        )


def specialist_prompt_version(role: AgentRole) -> str:
    try:
        return SPECIALIST_PROMPT_PROFILES[role].version
    except KeyError:
        raise ValueError(f"No specialist prompt is defined for role: {role.value}") from None


def agent_prompt_version(role: AgentRole) -> str:
    if role == AgentRole.GENERAL:
        return PROMPT_VERSION
    return specialist_prompt_version(role)
