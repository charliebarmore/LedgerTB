# Jev synthetic evaluation — September 17, 2026

Historical design and test evidence. Final qualification is recorded in the
[v1.8.0 release review](RELEASE-REVIEW-1.8.0.md).

Protocol and fixture were committed in `3300de6` before inference. The adapter and
evaluator were frozen in local `5dcd696` before the first held-out run. No release
or installation is implied by these results.

## First held-out result

Jev 1.13.0 matched **39/40** labeled outcomes. All **15 account suggestions** were
correct; the other 25 results required clarification, split allocation, or transfer
review. There were no invalid responses, wrong account suggestions, or errors at
the predefined concentration threshold of 0.8.

The single mismatch was `transfer_09`: “CARD PAYMENT REVERSAL,” with evidence that
the bank returned an unsuccessful payment of the company card balance. The label
was `transfer_review`; Jev chose `insufficient_information`, concentration 0.53.
This is retained as an error. Neither the label nor the frozen prompt was changed
after inspecting the held-out results. No account was filled or entry posted.

| Split/provider | Correct outcomes | Review outcomes/abstentions | Account suggestions | Wrong accounts | Confident errors | Invalid responses |
|---|---:|---:|---:|---:|---:|---:|
| Development, original Jev | 75/80 | 54 | 26 | 1 | 1 | 0 |
| Development, final Jev | 80/80 | 55 | 25 | 0 | 0 | 0 |
| Development, local patterns | 32/80 | 49 | 31 | 17 | 17 | 0 |
| Holdout, frozen Jev | 39/40 | 25 | 15 | 0 | 0 | 0 |
| Holdout, local patterns | 14/40 | 40 | 0 | 0 | 0 | 0 |

“Abstention” means no single account suggestion, including an explicit split or
transfer route. A wrong review route still counts as incorrect. Confidence is
distribution concentration, not a probability of accounting correctness. Every
suggestion requires human review regardless of concentration.

## Development findings and fixes

The original run incorrectly routed an ordinary card purchase as a transfer, an
unidentified partial refund as a split, a principal-plus-interest payment as a
transfer, and conflicting evidence as a confirmed split. It also suggested meals
for adversarial merchant text with no business-purpose evidence. The wrong account
had concentration 0.47; the confident error was a conflicting-evidence review route.

The revised instructions distinguish purchase facts from embedded commands,
conflicting explanations from confirmed components, and partial refunds from
evidenced reversals. These rules were adjusted using development responses only.

A revised run produced 75 correct validated results and five validation failures
in one atomic batch. A deliberate five-case diagnostic repeat reproduced a valid
answer distribution summing to 0.99 because values were rounded to hundredths.
Validation now accepts only narrowly bounded rounding-compatible totals, while
still rejecting unknown/missing IDs, nonfinite values, invalid winners, and material
normalization errors. The original probabilities are retained, not renormalized.
The diagnostic response replayed offline as five valid correct results after the
fix. The subsequent full live development run was 80/80.

Request reuse now hashes actual Choice criteria as well as evidence, ledger/client,
model and instructions. Large selections split below conservative byte budgets;
oversized individual evidence is left for local review without truncation or a paid
request. Transport failure stops unsent chunks. Reruns do not automatically retry.

## Latency, usage and cost evidence

| Live run | Requests | Input tokens | Output tokens | Runner elapsed seconds |
|---|---:|---:|---:|---:|
| Original development | 4 | 63,177 | 16,049 | 5.814 |
| Revised development, validation failure | 4 | 102,189 | 16,004 | 12.936 |
| Five-case diagnostic repeat | 1 | 7,074 | 1,000 | 1.195 |
| Final development | 7 | 89,951 | 16,033 | 79.990 |
| First holdout | 4 | 45,292 | 8,007 | 7.655 |

Total expanded evaluation: **20 deliberate requests, 307,683 input tokens and
57,093 output tokens**. Usage is counted once per request, including the failed
validation batch. API responses did not provide billed dollars; actual billing
was not checked. No live Anthropic run was made because no development credential
was supplied.

[TypeSafe's model documentation](https://docs.typesafe.ai/models), checked on
September 17 for this task, lists $0.042 per million input tokens and free output
tokens for Jev 1.13. At that published rate, the 20 expanded-evaluation requests
estimate to **$0.012923**; the first holdout alone estimates to **$0.001902**.
These are calculations from reported tokens, not verified charges, and exclude
the earlier 16-case smoke runs and any subsequent packaged-app checks.

Holdout batch latencies were 1.742, 3.097, 1.723 and 0.970 seconds. Final development
batch latencies ranged from 1.559 to 35.591 seconds on a heavily contended Mac.
These are client-observed wall times, including local processing and scheduling,
not isolated service latency. Per-row latency repeats its batch measurement and
must not be summed across rows. Socket timeout is not an end-to-end wall-time SLA.

## Limits and reproducible evidence

The 120 narratives and labels are assistant-authored, with 80 development and 40
held-out cases. This is not blinded accountant adjudication or production
calibration. Scenarios share an account taxonomy and general policy; merchant
descriptions are disjoint. Cases include supported purchases, ambiguous merchants,
refunds, transfers, mixed purchases, missing evidence, conflicts and adversarial
text. The holdout only guards against tuning on its responses.

Jev receives separate receipt evidence and transfer flags. The existing local
matcher sees descriptions and four seeded learned patterns; unfamiliar holdout
merchants all abstained. The existing Anthropic service has a different evidence
contract and lacks explicit split/transfer outcomes. This measures existing
product behaviors, not equal-information model superiority.

- [Protocol and exact commands](JEV-VALIDATION-PLAN.md)
- [Original development report](jev-evaluation-results/development-baseline.json)
- [Revised run with validation failures](jev-evaluation-results/development-revised.json)
- [Diagnostic response evidence](jev-evaluation-results/development-validation-diagnostic.json)
- [Final development report](jev-evaluation-results/development-final.json)
- [First held-out report](jev-evaluation-results/holdout.json)

Reports include fixture, evaluator, instruction and adapter hashes, row-level
labels/results, model ID and per-request usage. Inputs are exclusively fictional.
For checked-in artifacts, the original `request_key` field is named
`request_sha256` to distinguish a content fingerprint from an API credential.
Values and outcomes are unchanged; original reports remain in the local output
directory. The runner uses the clearer field name for future reports.
Further prompt tuning should use new development cases; this holdout is now
consumed. Firm data-processing approval, representative accountant-labeled pilots,
packaged platform acceptance, and separate release authorization remain rollout
requirements. Passing this evaluation never enables automatic posting.
