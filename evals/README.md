# AgentReview evaluation seed set

`review_seed_v1.json` is a deterministic, offline evaluation dataset. The default runner
uses a case-specific mock response to test the evaluator, matcher, and metric calculations.
Its result is an evaluator self-test, not a model-quality baseline.

The current code uses prompt version `review-v2`. Checked-in `review-v1` and `review-v2`
baselines preserve the before/after evidence for the same `seed-v1` dataset. Treat both files as
immutable historical references; create a new prompt or dataset version instead of overwriting
them.

The datasets have different roles:

- `review_seed_v1.json` is the development set used to diagnose and improve `review-v1`.
- `review_holdout_v1.json` is a frozen generalization check created after `review-v2`. It has 10
  disjoint cases, including two negatives and two multi-file cases. Do not tune the prompt or
  rewrite labels from its model results; create a new dataset version for future changes.
- `review_holdout_v2.json` is a corrected derivative of `holdout-v1`, not a new blind holdout.
  It changes only the known missing import in the contained-upload negative fixture and that
  fixture's synthetic head SHA. Use it for future regression checks while preserving v1 as the
  record of the first unseen run.

The first live `review-v2 + holdout-v1` baseline is checked in as
`deepseek-flash__review-v2__holdout-v1.json`. Preserve its raw evaluator output. Manual review
found two text-matcher misses and one supposedly negative fixture with a real missing-import
defect; these are documented separately instead of rewriting the frozen dataset or baseline.

Matcher behavior is versioned independently from the model and prompt. Historical baselines
without an explicit matcher field are interpreted as `text-v1`. New evaluator runs use
`text-v2`, which preserves same-file, one-to-one matching while adding deterministic operator
normalization and order-insensitive matching within a multi-word expected alternative. Category,
severity, and expected line range still do not influence finding alignment.

Re-score the saved Holdout findings with Matcher v2 without calling a model:

```powershell
python -m evals.rescore `
  --baseline evals/baselines/deepseek-flash__review-v2__holdout-v1.json `
  --dataset evals/cases/review_holdout_v1.json `
  --matcher-version text-v2
```

Add `--save` to write the default immutable artifact under `evals/rescores/`, or combine
`--save --output NEW_PATH` to select a new file. Saving refuses to overwrite an existing
artifact. The command validates the dataset version, SHA-256, and case IDs; it reads only local
JSON and does not load application settings or access the network, database, GitHub, or an LLM.

The checked-in `text-v2` Holdout rescore is bound to the source baseline by SHA-256. It raises
automated recall from `0.75` to `1.0` and reduces false positives from `3` to `1` by fixing the
two confirmed textual alignment misses. The remaining false positive is intentionally retained:
the supposedly negative fixture contains a real missing import, so suppressing it in the matcher
would hide a dataset defect.

Run the corrected derivative's offline evaluator self-test with:

```powershell
python -m evals.runner --dataset evals/cases/review_holdout_v2.json
```

The v2 dataset has its own frozen SHA-256 and a lineage test proving that the other nine cases,
all expected findings, and the path-containment implementation are unchanged. Do not compare
v1 and v2 scores as if they came from the same dataset: their SHA-256 identities differ, and v2
was created after inspecting the first v1 model result.

The holdout file is guarded by a SHA-256 assertion in the unit tests. Run its offline evaluator
self-test with:

```powershell
python -m evals.runner --dataset evals/cases/review_holdout_v1.json
```

The command used to create a guarded live holdout baseline is:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_holdout_v1.json `
  --provider deepseek `
  --live `
  --save-baseline
```

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

Select one exact case for a deterministic low-cost trial:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_holdout_v2.json `
  --provider deepseek `
  --live `
  --case-id holdout-negative-contained-upload-path
```

`--case-id` and `--max-cases` are mutually exclusive. Unknown IDs are rejected before Git state,
application settings, or a real provider are loaded. An exact-case run is partial, reports
`is_model_baseline=false`, and cannot be combined with `--save-baseline`.

The corrected contained-upload negative case was exercised once with the real DeepSeek provider.
It returned a schema-valid response with zero findings and zero false positives, using 1,127 input
tokens and 1,625 output tokens. This is integration evidence for the fixture repair and exact-case
selection only: it was a dirty-worktree partial trial, is not stored as a model baseline, and its
single 7.88-second latency sample must not be treated as an SLO or aggregate performance result.

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
