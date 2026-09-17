# AgentReview evaluation seed set

`review_seed_v1.json` is a deterministic, offline evaluation dataset. The default runner
uses a case-specific mock response to test the evaluator, matcher, and metric calculations.
Its result is an evaluator self-test, not a model-quality baseline.

Run it from the repository root:

```powershell
python -m evals.runner
```

The default command does not load application settings and does not access the network,
database, GitHub, or a paid model. Live DeepSeek evaluation and model baseline persistence
are deliberately deferred to a later implementation batch. Supplying a non-mock provider
without `--live` is rejected, and `--provider deepseek --live` currently exits with a clear
not-implemented error before loading provider configuration.

Expected source ranges are retained as ground truth for future line-level review support.
They are not scored in this version because the production finding schema has no line fields.
