from pydantic import BaseModel, ConfigDict, Field


class GitHubUserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(min_length=1)


class GitHubRefResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str = Field(min_length=7, max_length=64)


class GitHubPullRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(gt=0)
    title: str = Field(min_length=1)
    body: str | None = None
    draft: bool
    user: GitHubUserResponse | None
    base: GitHubRefResponse
    head: GitHubRefResponse


class GitHubChangedFileResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1)
    status: str = Field(min_length=1)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None
    previous_filename: str | None = None


class GitHubPullRequestReviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    body: str | None = None
    state: str = Field(min_length=1)
    html_url: str | None = None
    commit_id: str = Field(min_length=7, max_length=64)
