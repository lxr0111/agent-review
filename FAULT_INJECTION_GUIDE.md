# Phase 1 Retry and Recovery Fault-Injection Guide

This guide validates the real HTTP transport, LLM error mapping, Celery retry scheduling,
pipeline recovery, MySQL persistence, and GitHub publication behavior without calling a
paid model API.

The fault setup is opt-in. Normal `compose.yml` behavior and the production providers are
unchanged. `compose.fault.yml` points only the Worker at a local Responses API stub and uses
a clearly fake API key.

## 1. Behavior under test

The current worker configuration uses:

- `REVIEW_WORKER_MAX_RETRIES=3`: one initial execution plus at most three retries;
- `REVIEW_RETRY_BASE_SECONDS=5` and `REVIEW_RETRY_MAX_SECONDS=300` normally;
- exponential delay `base * 2 ** retries`, capped at the maximum;
- random jitter of up to 25 percent;
- the greater of exponential delay and `Retry-After` for LLM HTTP 429 responses.

The fault override shortens the base delay to one second and the maximum to four seconds.
With a one-second base, integer rounding makes the first two observed delays exactly one
and two seconds despite jitter.

Each pipeline execution calls `begin_attempt`, so `review_tasks.attempt_count` counts actual
pipeline attempts. A retryable error leaves the task running until a later attempt succeeds
or retries are exhausted. A permanent error immediately marks it failed.

Before calling the model, each attempt loads `review_reports` by task ID. If a report already
exists, context construction and model invocation are skipped and the pipeline resumes at
publication. Publication has its own persisted attempt counter and remote-marker
reconciliation.

## 2. Start the isolated fault environment

Keep Docker Desktop and the Smee forwarding process running. From the project root:

```powershell
docker compose -f compose.yml -f compose.fault.yml up -d --build llm-fault-stub worker
docker compose -f compose.yml -f compose.fault.yml ps
```

Confirm that the Worker is using the stub without printing the key:

```powershell
docker compose -f compose.yml -f compose.fault.yml exec -T worker python -c "from app.core.config import get_settings; s=get_settings(); print({'provider': s.llm_provider, 'base_url': s.llm_base_url, 'model': s.llm_model, 'key_configured': s.llm_api_key is not None})"
```

Expected values:

```text
provider: deepseek
base_url: http://llm-fault-stub:8090/
model: fault-injection-model
key_configured: True
```

Observe both services in a separate terminal:

```powershell
docker compose -f compose.yml -f compose.fault.yml logs -f worker llm-fault-stub
```

For every scenario below, configure the stub first, then push a unique commit to the test
PR. Reusing a head SHA will hit task idempotency and will not run another review.

## 3. Read-only observations

Inspect the latest task for the test PR after each run:

```powershell
docker compose -f compose.yml -f compose.fault.yml exec -T worker python scripts/inspect_retry_state.py --repository lxr0111/agent-review-test --pr 1
```

The script starts a MySQL `READ ONLY` transaction, prints safe fields, and rolls it back.
It never prints error messages, request bodies, URLs containing credentials, or secrets.

Inspect the stub counters:

```powershell
Invoke-RestMethod http://localhost:8091/__fault__/state | ConvertTo-Json
```

Relevant Worker log events:

- `review_pipeline_retry_scheduled`: retry number, countdown, and error code;
- `review_pipeline_finished`: final successful outcome;
- `review_pipeline_failed`: permanent failure or exhausted retries.

Relevant database fields:

- `review_tasks.status`, `current_stage`, `attempt_count`, and `error_code`;
- number and model name of the linked `review_reports` row;
- number, status, and `attempt_count` of the linked `review_publications` row.

## 4. Timeout once, recover on the second call

```powershell
$body = @{scenario='timeout'; failures_before_success=1; timeout_seconds=2} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Push a new test commit. Expected result:

- first client request times out after one second;
- log error code is `llm_transient_error` with `countdown_seconds=1`;
- stub counters are two requests, one failure, one success;
- task is `COMPLETED`, `attempt_count=2`;
- exactly one Report and one `PUBLISHED` Publication exist;
- Publication `attempt_count=1` because failure occurred before report publication;
- the PR receives exactly one new Review for this head SHA.

## 5. HTTP 429 once, recover on the second call

```powershell
$body = @{scenario='rate_limit'; failures_before_success=1; retry_after_seconds=2} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Push a new commit. Expected result:

- log error code is `llm_rate_limit_error`;
- `countdown_seconds=2`, proving `Retry-After` overrides the one-second backoff;
- stub request count is two;
- task is `COMPLETED`, `attempt_count=2`;
- Report count and Publication count are both one;
- the PR receives one Review.

## 6. HTTP 503 once, recover on the second call

```powershell
$body = @{scenario='server_error'; failures_before_success=1; server_error_status=503} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Push a new commit. Expected result:

- log error code is `llm_transient_error` with `countdown_seconds=1`;
- stub request count is two;
- task is `COMPLETED`, `attempt_count=2`;
- one Report, one Publication, and one GitHub Review exist.

## 7. Demonstrate exponential backoff

This additional case fails twice and succeeds on the third call:

```powershell
$body = @{scenario='server_error'; failures_before_success=2; server_error_status=500} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Push a new commit. Expected retry logs have countdowns of one second and two seconds.
The final task has `attempt_count=3`; stub counters are three requests, two failures, and one
success. Report, Publication, and GitHub Review counts remain one.

## 8. Permanent errors must not retry

Use a new commit for each scenario you want to retain as evidence.

### HTTP 401

```powershell
$body = @{scenario='unauthorized'} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Expected: `FAILED`, `attempt_count=1`, `error_code=llm_authentication_error`, stub request
count one, no retry-scheduled log, no Report, no Publication, and no GitHub Review.

### HTTP 403

```powershell
$body = @{scenario='forbidden'} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

Expected: `FAILED`, `attempt_count=1`, `error_code=llm_permission_error`, one stub request,
and no Report, Publication, or GitHub Review.

### Invalid structured output

```powershell
$body = @{scenario='invalid_schema'} | ConvertTo-Json
Invoke-RestMethod http://localhost:8091/__fault__/configure -Method Post -ContentType 'application/json' -Body $body
```

The stub returns HTTP 200 with JSON that omits required review fields. Expected: `FAILED`,
`attempt_count=1`, `error_code=llm_output_error`, one stub request, no retry, and no Report,
Publication, or GitHub Review.

## 9. Report-persisted publication recovery

An automated persistence test injects a transient GitHub publication failure after the
Report commit. Its second pipeline attempt must not call the LLM or rebuild context:

```powershell
conda activate agent-review
python -m pytest tests/unit/test_review_pipeline_service.py -k retries_publication_without_calling_ai_again
```

Run this command from the project root on the host, not inside the production Worker
container. The production image intentionally excludes development dependencies and the
`tests/` directory. This unit test uses in-memory SQLite and fake GitHub/LLM providers, so it
does not require Docker services and does not call DeepSeek or GitHub.

The test verifies:

- task attempts advance from one to two;
- the model prompt count remains one;
- changed files are fetched only once;
- the single Report survives the failure;
- the same Publication advances from `FAILED` attempt one to `PUBLISHED` attempt two;
- only one Publication row exists.

## 10. Restore the real DeepSeek Worker

After all drills:

```powershell
docker compose up -d --force-recreate worker
docker compose -f compose.yml -f compose.fault.yml stop llm-fault-stub
docker compose ps
```

Run the safe configuration check again and confirm that `base_url` is the real DeepSeek
endpoint before the next real review. Do not use `docker compose config` or print the full
container environment because those commands can expose resolved secrets.
