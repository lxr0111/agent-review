from collections.abc import Iterable

from app.providers.llm.protocol import LLMProvider
from app.services.ai_review_service import AIReviewService
from app.services.review_agent import (
    AgentRole,
    GeneralReviewAgent,
    ReviewAgent,
    SpecialistReviewAgent,
)
from app.services.specialist_prompt_builder import (
    SPECIALIST_PROMPT_PROFILES,
    SpecialistReviewPromptBuilder,
)


class ReviewAgentRegistry:
    def __init__(self, agents: Iterable[ReviewAgent]) -> None:
        registered: dict[AgentRole, ReviewAgent] = {}
        for agent in agents:
            if agent.role in registered:
                raise ValueError(f"Duplicate review agent role: {agent.role.value}")
            registered[agent.role] = agent
        if not registered:
            raise ValueError("Review agent registry requires at least one agent")
        self._agents = registered

    @property
    def roles(self) -> tuple[AgentRole, ...]:
        return tuple(self._agents)

    def get(self, role: AgentRole) -> ReviewAgent:
        try:
            return self._agents[role]
        except KeyError:
            raise KeyError(f"Review agent role is not registered: {role.value}") from None


def create_default_agent_registry(provider: LLMProvider) -> ReviewAgentRegistry:
    agents: list[ReviewAgent] = [GeneralReviewAgent(AIReviewService(provider))]
    for role in SPECIALIST_PROMPT_PROFILES:
        agents.append(
            SpecialistReviewAgent(
                role=role,
                review_service=AIReviewService(
                    provider,
                    prompt_builder=SpecialistReviewPromptBuilder(role),
                ),
            )
        )
    return ReviewAgentRegistry(agents)
