# Jev validation protocol — September 17–18, 2026

Current work and priorities: [LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
Implementation baseline: local commit `6c31483`. This protocol is committed before
expanded live inference. It does not authorize a release or installed-app change.

## Evaluation design

`tests/fixtures/jev_evaluation_v2.json` contains 120 distinct fictional narratives:
80 development and 40 held-out cases, stratified across eight scenario families.
Each has an expected account/review outcome and rationale. Merchant descriptions
are disjoint across splits. The contexts and account taxonomy are documented in the
fixture. No real client information is used.

These are assistant-authored labels, not independent accountant adjudication. The
same assistant authored the cases and integration; this is **not a blinded external
benchmark**. The holdout protects against response-driven tuning: inspect development
responses first, freeze improvements, then evaluate held-out responses. Do not adjust
labels to agree with the model. Resolve any annotation defect explicitly, preserving
its original result and explaining the correction. If held-out failures drive further
changes, retain the first holdout result and identify that set as consumed.

The input allowlist excludes labels, rationales, case IDs and split metadata. Jev gets
separate receipt evidence and transfer flags; the existing local pattern matcher
uses descriptions only. The existing Anthropic service uses descriptions and business
context and has no explicit split/transfer outcomes. Report those differences;
comparison is of existing product behavior, not equal model capability or calibration.

## Measurements and limits

- Exact labeled outcome agreement, all review outcomes, account suggestion precision,
  wrong account suggestions, missed account suggestions and review-route mismatches.
- Confident errors at the preexisting concentration/confidence threshold of 0.8.
  This is a diagnostic count, never a posting threshold or calibrated cross-provider
  probability comparison.
- Errors separately from abstention; latency per actual batch and total elapsed time;
  token usage counted once per request; billed dollars unknown unless independently
  available. Record fixture, evaluator and service hashes plus source commit.
- Development first; held-out inference after a development freeze. No automatic
  retries. At most 16 requests per CLI invocation by default. Checkpoint each batch.
  Use the development key from `~/.typesafe.env` without printing or exporting it.

An unchanged repeat in the application must cause no new request. Suggestions must
never post, change inclusion, invent an account, or bypass engine permissions.
These are absolute software acceptance criteria. Model accuracy remains descriptive:
120 synthetic cases cannot establish production calibration. All suggestions still
require human review, even if every case passes.

## Reproduction

```sh
.macos-venv/bin/python -m pytest -q tests/test_jev_comparison.py
.macos-venv/bin/python scripts/compare_jev.py \
  --fixture tests/fixtures/jev_evaluation_v2.json --split development \
  --output output/jev-overnight-20260917/development-local.json
# Explicit paid synthetic run, never automatically retried:
.macos-venv/bin/python scripts/compare_jev.py \
  --fixture tests/fixtures/jev_evaluation_v2.json --split development --live-jev \
  --output output/jev-overnight-20260917/development-baseline.json
# Run holdout only after the development implementation is frozen:
.macos-venv/bin/python scripts/compare_jev.py \
  --fixture tests/fixtures/jev_evaluation_v2.json --split holdout --live-jev \
  --output output/jev-overnight-20260917/holdout.json
```

After evaluation, test larger imports, changed accounts/evidence/context, interrupted
requests, retries, switching ledgers and offline review with fake providers/vaults and
disposable encrypted books. Exercise a separate Mac bundle through synthetic UI and
posting, not just its import selfcheck. Preserve the installed application. Windows
native and installed-upgrade acceptance cannot be established on this Mac; record
those limits explicitly. The authorized follow-on import review work keeps bulk row
selection, cloud-request selection and inclusion for posting distinct.
