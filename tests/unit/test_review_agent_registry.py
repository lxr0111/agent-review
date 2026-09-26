import pytest

from app.domain.review.entities import ChangedFile, PullRequestSnapshot, ReviewContext
from app.domain.review.enums import FileChangeStatus
from app.providers.llm.mock import MockLLMProvider
from app.services.ai_review_service import AIReviewService
from app.services.review_agent import AgentRole, GeneralReviewAgent, ReviewAgent
from app.services.review_agent_registry import (
    ReviewAgentRegistry,
    create_default_agent_registry,
)
from app.services.specialist_prompt_builder import specialist_prompt_version


def _context() -> ReviewContext:
    changed_file = ChangedFile(
        path="src/users.py",
        status=FileChangeStatus.MODIFIED,
        additions=1,
        deletions=1,
        changes=2,
        patch="@@ -1 +1 @@\n-old_query\n+cursor.execute(query)",
    )
    return ReviewContext(
        pull_request=PullRequestSnapshot(
            repository="owner/repository",
            number=7,
            title="Update user lookup",
            body=None,
            base_sha="base-sha",
            head_sha="head-sha",
            draft=False,
            author_login="octocat",
        ),
        reviewable_files=(changed_file,),
        skipped_files=(),
        total_patch_characters=len(changed_file.patch or ""),
    )


def test_default_registry_contains_general_and_four_specialists() -> None:
    registry = create_default_agent_registry(MockLLMProvider())

    assert registry.roles == (
        AgentRole.GENERAL,
        AgentRole.CORRECTNESS,
        AgentRole.SECURITY,
        AgentRole.PERFORMANCE,
        AgentRole.MAINTAINABILITY,
    )
    assert all(isinstance(registry.get(role), ReviewAgent) for role in registry.roles)


@pytest.mark.parametrize(
    ("role", "assignment", "category_instruction"),
    [
        (AgentRole.CORRECTNESS, "correctness and reliability", "correctness or reliability"),
        (AgentRole.SECURITY, "Specialist assignment: security", "security category"),
        (AgentRole.PERFORMANCE, "Specialist assignment: performance", "performance category"),
        (
            AgentRole.MAINTAINABILITY,
            "Specialist assignment: objective maintainability",
            "maintainability category",
        ),
    ],
)
def test_specialist_agent_uses_independent_versioned_prompt(
    role: AgentRole,
    assignment: str,
    category_instruction: str,
) -> None:
    provider = MockLLMProvider()
    agent = create_default_agent_registry(provider).get(role)

    result = agent.review(_context())

    assert result.role == role
    assert result.report.prompt_version == specialist_prompt_version(role)
    assert agent.prompt_version == specialist_prompt_version(role)
    assert provider.prompts[-1].version == specialist_prompt_version(role)
    assert assignment in provider.prompts[-1].system
    assert category_instruction in provider.prompts[-1].system
    assert result.usage is None


def test_registry_rejects_duplicate_roles_and_reports_missing_role() -> None:
    provider = MockLLMProvider()
    first = GeneralReviewAgent(AIReviewService(provider))
    second = GeneralReviewAgent(AIReviewService(provider))

    with pytest.raises(ValueError, match="Duplicate review agent role"):
        ReviewAgentRegistry((first, second))

    registry = ReviewAgentRegistry((first,))
    with pytest.raises(KeyError, match="not registered"):
        registry.get(AgentRole.SECURITY)


def test_registry_rejects_empty_configuration() -> None:
    with pytest.raises(ValueError, match="at least one agent"):
        ReviewAgentRegistry(())
