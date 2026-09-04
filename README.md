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
