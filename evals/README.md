# AgentReview evaluation seed set

`review_seed_v1.json` is a deterministic, offline evaluation dataset. The default runner
uses a case-specific mock response to test the evaluator, matcher, and metric calculations.
Its result is an evaluator self-test, not a model-quality baseline.

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

Expected source ranges are retained as ground truth for future line-level review support.
They are not scored in this version because the production finding schema has no line fields.
