from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GitHubRef(BaseModel):
    sha: str = Field(min_length=7, max_length=64)


class GitHubPullRequest(BaseModel):
    number: int = Field(gt=0)
    base: GitHubRef
    head: GitHubRef


class GitHubRepository(BaseModel):
    id: int = Field(gt=0)
    full_name: str = Field(min_length=1, max_length=255)


class GitHubInstallation(BaseModel):
    id: int = Field(gt=0)


class GitHubPullRequestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    number: int = Field(gt=0)
    pull_request: GitHubPullRequest
    repository: GitHubRepository
    installation: GitHubInstallation


class WebhookResponse(BaseModel):
    status: Literal["accepted", "duplicate", "ignored", "pong"]
    delivery_id: str
    review_task_id: UUID | None = None
