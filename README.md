# AgentReview

AgentReview is an AI-powered GitHub pull request review platform. The current version
provides a complete asynchronous flow from GitHub webhook ingestion through diff
selection, structured AI review, MySQL persistence, and GitHub summary publication.

## Requirements

- Python 3.12
- Docker Desktop with Docker Compose
- Conda (optional, used for local Python isolation)

## Local setup

```powershell
conda activate agent-review
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Run all services in containers:

```powershell
docker compose up --build
```

Open the API documentation at <http://localhost:8000/docs>.

## GitHub webhook ingestion

Configure the same random secret in `GITHUB_WEBHOOK_SECRET` and in the GitHub webhook
settings. GitHub sends pull request events to:

```text
POST /api/v1/webhooks/github
```

The endpoint verifies `X-Hub-Signature-256` against the exact request bytes, accepts
the `opened`, `synchronize`, and `reopened` pull request actions, and records every
supported or ignored delivery in `webhook_events`. The unique
`(provider, delivery_id)` constraint prevents GitHub retries from creating duplicate
events, while `(repository, pull_request_number, head_sha)` prevents duplicate review
tasks for the same commit. Raw webhook payloads are not persisted; only a SHA-256
digest is retained for audit purposes.

## GitHub App provider

The GitHub provider uses short-lived installation access tokens rather than a personal
access token. Configure the GitHub App with read access to repository contents and
read/write access to pull requests. Set `GITHUB_APP_CLIENT_ID`, place the private key
under `secrets/`, and set `GITHUB_PRIVATE_KEY_PATH` to its path. Docker mounts this
directory read-only into the worker. The private key must remain outside version
control; `secrets/` and `*.pem` are ignored by Git.

The provider supports fetching pull request metadata, changed files, and existing pull
request reviews, as well as publishing a summary review for an explicit commit. API
responses are validated, pagination remains on the configured GitHub origin, and a
`401` triggers one installation-token refresh.

Review publication is tracked separately from report generation. Before posting, the
service verifies that the pull request still points to the task's head commit. A stable
hidden marker allows retries to reconcile a GitHub review created before its database
receipt was saved, avoiding duplicate bot comments. AI reviews are published as
non-blocking `COMMENT` reviews; automatic approval and merge blocking are deliberately
out of scope.

## Worker pipeline and retries

The Celery worker executes the application pipeline rather than embedding business
logic in the task function. Each delivery records an attempt, and a retry resumes from
an existing report instead of paying for the same AI analysis again. Completed, stale,
cancelled, and failed tasks are terminal and are not reprocessed.

GitHub and AI rate limits, temporary upstream failures, network timeouts, and transient
database connection errors use bounded exponential backoff with jitter. Provider
`Retry-After` and GitHub rate-limit reset values are honored. Permanent errors fail
immediately, while a changed pull request head marks the task stale.

Use `GET /api/v1/reviews/{task_id}` with the task ID returned by the webhook endpoint
to observe `current_stage`, `attempt_count`, and the terminal status.

## AI review provider

AI review uses a provider interface so application services do not depend on a model
vendor. Local development defaults to the deterministic mock provider and makes no
paid API requests. To use the OpenAI Responses API, configure a model that supports
structured outputs and provide the API key only through the environment:

```text
LLM_PROVIDER=openai
LLM_MODEL=your-structured-output-capable-model
OPENAI_API_KEY=your-api-key
```

The OpenAI adapter requests a strict JSON Schema response, disables response storage,
validates the returned report with Pydantic, and classifies authentication, permission,
rate-limit, transient, refusal, and invalid-output failures. Model names are deliberately
not hard-coded. Automated tests use an HTTP mock and never call the paid API.

## Quality checks

```powershell
ruff check .
ruff format --check .
mypy app
pytest
```

## Architecture

- `app/api`: HTTP routes and dependency wiring
- `app/domain`: framework-independent state rules
- `app/services`: application use cases and transaction orchestration
- `app/repositories`: persistence operations
- `app/providers`: GitHub, LLM, and parser adapters
- `app/workers`: Celery entry points

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the complete roadmap.
