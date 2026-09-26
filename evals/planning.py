from pydantic import BaseModel, ConfigDict, Field

from app.services.review_agent import AgentRole
from app.services.review_planner import DeterministicReviewPlanner
from evals.schemas import EvalCase, EvalDataset


class PlannedAgentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    reasons: tuple[str, ...] = Field(min_length=1)


class CasePlanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    agents: tuple[PlannedAgentSnapshot, ...] = Field(min_length=1)


class AgentPlanningRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_kind: str = "agent_planning"
    planner_version: str = "deterministic-v1"
    schema_version: str
    dataset_version: str
    dataset_case_count: int = Field(ge=1)
    executed_case_count: int = Field(ge=1)
    cases: tuple[CasePlanningResult, ...] = Field(min_length=1)


def build_agent_planning_run(
    dataset: EvalDataset,
    cases: tuple[EvalCase, ...],
    *,
    planner: DeterministicReviewPlanner | None = None,
) -> AgentPlanningRun:
    selected_planner = planner or DeterministicReviewPlanner()
    planned_cases: list[CasePlanningResult] = []
    for case in cases:
        plan = selected_planner.plan(case.to_review_context())
        planned_cases.append(
            CasePlanningResult(
                case_id=case.id,
                agents=tuple(
                    PlannedAgentSnapshot(role=agent.role, reasons=agent.reasons)
                    for agent in plan.agents
                ),
            )
        )
    return AgentPlanningRun(
        schema_version=dataset.schema_version,
        dataset_version=dataset.dataset_version,
        dataset_case_count=len(dataset.cases),
        executed_case_count=len(cases),
        cases=tuple(planned_cases),
    )
