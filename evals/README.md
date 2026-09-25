# AgentReview evaluation seed set

`review_seed_v1.json` is a deterministic, offline evaluation dataset. The default runner
uses a case-specific mock response to test the evaluator, matcher, and metric calculations.
Its result is an evaluator self-test, not a model-quality baseline.

The current code uses prompt version `review-v2`. Checked-in `review-v1` and `review-v2`
baselines preserve the before/after evidence for the same `seed-v1` dataset. Treat both files as
immutable historical references; create a new prompt or dataset version instead of overwriting
them.

Run it from the repository root:

```powershell
python -m evals.runner
```

The default command does not load application settings and does not access the network,
database, GitHub, or a paid model. Supplying a non-mock provider without `--live` is rejected.

Run one paid DeepSeek case as a guarded trial:

```powershell
python -m evals.runner --provider deepseek --live --max-cases 1
```

Run all cases without writing a baseline:

```powershell
python -m evals.runner --provider deepseek --live
```

Save a full model baseline:

```powershell
python -m evals.runner --provider deepseek --live --save-baseline
```

Baseline saving is rejected for mock and partial runs. It also requires a clean Git worktree
by default and refuses to overwrite an existing file. `--allow-dirty` is available only as an
explicit escape hatch, and `--baseline-output PATH` can select a different new output path.
Application settings are loaded only after live-mode validation. The runner calls neither the
database nor GitHub, and it never accepts an API key as a command-line argument.

Saved baselines contain model findings, matcher results, metrics, latency, API-reported token
usage, dataset SHA-256, Git commit and dirty state. They do not contain source patches, prompts,
environment variables, credentials, provider error messages, or hard-coded model prices. Cost
remains `null` until a separate versioned price snapshot is implemented.

Compare two saved baselines offline:

```powershell
python -m evals.compare `
  --baseline evals/baselines/deepseek-flash__review-v1__seed-v1.json `
  --candidate evals/baselines/deepseek-flash__review-v2__seed-v1.json
```

Comparison requires identical evaluation schema versions, dataset versions, dataset SHA-256
values, and case IDs. It derives precision, F1, and negative-sample case pass rate without
rewriting old baseline files. It also reports metric deltas plus added, removed, and relabeled
findings for each case. Actual findings are aligned by file and normalized text only; category
and severity are compared after alignment and do not influence matching. Delta statuses are
informational in this version and are not release gates. The comparison command reads local JSON
only and does not load application settings or access the network, database, GitHub, or an LLM.

Expected source ranges are retained as ground truth for future line-level review support.
They are not scored in this version because the production finding schema has no line fields.
