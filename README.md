# AgentReview

AgentReview is a multi-agent GitHub pull request review platform. The current version
provides a FastAPI API, MySQL persistence, a Redis-backed Celery worker, task state
transitions, health checks, and the first GitHub webhook ingestion flow.

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

Create a simulated review:

```powershell
$body = @{
  repository = "example/project"
  pull_request_number = 1
  head_sha = "0123456789abcdef"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/reviews `
  -ContentType "application/json" -Body $body
```

Use `GET /api/v1/reviews/{task_id}` to observe the task reach `completed`.

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
access token. Set `GITHUB_APP_CLIENT_ID` and `GITHUB_PRIVATE_KEY_PATH` when you are
ready to connect a real GitHub App. The private key must remain outside version
control; `secrets/` and `*.pem` are ignored by Git.

The provider currently supports fetching pull request metadata and all changed files,
including pagination, typed response validation, one token refresh after a `401`, and
safe error categories for authentication, permissions, rate limits, missing resources,
and transient failures. It is not connected to the Celery review pipeline yet.

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
