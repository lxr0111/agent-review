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
- `review_aggregation_v1.json` is a four-case multi-agent development challenge set. It covers
  overlapping SQL-injection/N+1 findings, adjacent path-traversal/resource-lifecycle defects,
  cross-file performance/maintainability findings, and a routed negative sample. It is not a
  holdout or model baseline; its purpose is to exercise planning, identity, aggregation audit,
  and contribution metrics before live multi-agent experiments.
- `review_architecture_security_holdout_v1.json` is the first frozen architecture-comparison
  shard. Its four previously unused cases cover one mixed security/correctness change, two
  security-only changes, and one security negative. All four deterministically route to
  Correctness plus Security, allowing the same complete shard to be evaluated by General,
  Security-only, and Planned Multi-Agent modes. Its frozen SHA-256 is
  `ef6c5dc7a804b4c89a4b051c664006d7145943f99b6ae25d526db7d95b41873e`. Do not tune prompts,
  matchers, planning, labels, or fixtures from its live results; create a new version instead.

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

Run the complete Planner -> specialist agents -> deterministic aggregator path with the offline
mock provider:

```powershell
python -m evals.runner --multi-agent --case-id security-sql-injection
```

Run the complete offline aggregation challenge set with:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_aggregation_v1.json `
  --multi-agent
```

The deterministic self-test executes 10 planned agent calls across four cases, reduces 16 raw
fixture findings to six expected findings, and keeps mock usage `null`. Dedicated scripted-provider
tests separately create partial overlap, Category/Severity conflicts and role-specific empty
outputs; these remain evaluator/aggregator tests rather than evidence of model quality.

This mode records planned roles and reasons, per-agent prompt versions, latency, raw findings and
usage, plus pre/post-aggregation finding counts, duplicate rate and agent contributions. Each
aggregation decision includes `kept`/`merged`, the source role, canonical fingerprint and reason;
each category or severity conflict includes all observed values, the selected value and reason.
This makes the final report traceable back to each specialist without exposing prompts or source
patches as separate fields. Model findings can still quote small code spans, so saved experiment
output should be handled with the same care as ordinary review output. Mock usage remains `null`;
this command is a plumbing self-test, not model-quality evidence. `--multi-agent` cannot be
combined with `--agent-role`, `--plan-only`, or `--save-baseline`.

Multi-agent runs also record `finding_identity_version`. `finding-text-v4` preserves the original
high-threshold lexical match, the v2 title-plus-code path, and the v3 explanation-root-cause
fallback. It adds a narrower fallback for findings that frame the impact differently: both
findings must reference the same file, cite compatible inline code for the defective expression,
and cite compatible inline code for the remediation. Source order and operators are preserved by
this code-span comparison. Category, severity and confidence never determine identity. When this
new evidence path groups conflicting categories, aggregation ranks confidence before role
alignment and records that choice in the decision audit instead of treating a specialist label as
proof of impact. Historical single-agent baselines do not have this field and load it as `null`;
historical multi-agent output keeps its recorded identity version.

A real multi-agent partial trial requires all explicit safety flags:

```powershell
python -m evals.runner `
  --provider deepseek `
  --live `
  --multi-agent `
  --case-id security-sql-injection
```

Multi-agent baseline saving remains disabled until the execution and recovery schema is stable.
The live path is sequential in this version, never accesses the database or GitHub, and is not
used by the production worker.

Save any evaluator run as an immutable experiment artifact with explicit `--output`. This is
separate from `--save-baseline`: partial and multi-agent runs remain non-baseline experiments,
and an existing output file is rejected before a real Provider is created and is never
overwritten. The recommended local directory is ignored by Git:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_aggregation_v1.json `
  --provider deepseek `
  --live `
  --agent-role general `
  --case-id aggregation-adjacent-path-and-resource `
  --output evals/results/general.json
```

Repeat the same committed case for one explicit specialist and Planned Multi-Agent, changing only
the execution mode and output path:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_aggregation_v1.json `
  --provider deepseek `
  --live `
  --agent-role security `
  --case-id aggregation-adjacent-path-and-resource `
  --output evals/results/security.json

python -m evals.runner `
  --dataset evals/cases/review_aggregation_v1.json `
  --provider deepseek `
  --live `
  --multi-agent `
  --case-id aggregation-adjacent-path-and-resource `
  --output evals/results/multi-agent.json
```

Then compare the three saved artifacts without loading application settings or accessing the
network, database, GitHub, or an LLM:

```powershell
python -m evals.compare `
  --general-artifact evals/results/general.json `
  --specialist-artifact evals/results/security.json `
  --multi-agent-artifact evals/results/multi-agent.json
```

The architecture comparison requires DeepSeek live artifacts with the same provider, model,
dataset version and SHA-256, matcher, Git commit, worktree dirty flag, and exact case IDs. It
reports pairwise quality, error, latency and token deltas plus multi-agent contribution,
incremental matched-findings-per-1,000-extra-token evidence, and per-case
`improved/regressed/mixed/unchanged` reasons. A matching dirty flag cannot prove that uncommitted
files were identical, so clean committed runs remain the standard for a formal comparison.
Artifact JSON may contain model-generated code excerpts and should be handled like ordinary
review output. `--output` is rejected with `--save-baseline` and `--plan-only`.

After committing the frozen Security shard and obtaining a clean worktree, collect the first
complete architecture comparison with:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v1.json `
  --provider deepseek --live --agent-role general `
  --output evals/results/security-holdout-general.json

python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v1.json `
  --provider deepseek --live --agent-role security `
  --output evals/results/security-holdout-specialist.json

python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v1.json `
  --provider deepseek --live --multi-agent `
  --output evals/results/security-holdout-multi-agent.json

python -m evals.compare `
  --general-artifact evals/results/security-holdout-general.json `
  --specialist-artifact evals/results/security-holdout-specialist.json `
  --multi-agent-artifact evals/results/security-holdout-multi-agent.json
```

These are paid live runs and are intentionally not executed by automated tests. The comparison
must be interpreted as a frozen four-case Security shard, not a production-wide quality or
latency benchmark.

The v1 shard has now been used to develop Security Prompt v2 and Finding Identity v4. Keep its
artifacts as historical evidence and regression input; do not describe another v1 run as an
unseen comparison. The next clean architecture experiment uses the independently frozen v2
shard. Commit the dataset first, verify a clean worktree, and then use distinct immutable output
paths:

```powershell
python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v2.json `
  --provider deepseek --live --agent-role general `
  --output evals/results/security-holdout-v2-general.json

python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v2.json `
  --provider deepseek --live --agent-role security `
  --output evals/results/security-holdout-v2-specialist.json

python -m evals.runner `
  --dataset evals/cases/review_architecture_security_holdout_v2.json `
  --provider deepseek --live --multi-agent `
  --output evals/results/security-holdout-v2-multi-agent.json

python -m evals.compare `
  --general-artifact evals/results/security-holdout-v2-general.json `
  --specialist-artifact evals/results/security-holdout-v2-specialist.json `
  --multi-agent-artifact evals/results/security-holdout-v2-multi-agent.json
```

The v2 shard contains four new cases: a mixed unverified-JWT/pagination change, predictable
password-reset randomness, bearer-token URL exposure, and a high-entropy reset-token negative.
Its mock runs only validate evaluator plumbing. Model-quality conclusions require the three
explicit paid runs above and must remain scoped to this shard.

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
