# Optional TypeSafe Jev categorization

Implementation and local verification: 2026-09-17. Current business priorities and
rollout decisions live on the [LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
This is a source implementation, not a released or installed application update.

## Use

1. In **Firm Settings → AI categorization**, select **TypeSafe Jev**, save the
   provider, and save your own TypeSafe key. Keys use `utils/secure_store.py` and
   the OS credential vault. The desktop app never reads `~/.typesafe.env`.
2. Load staged transactions through **Import Transactions → Review & Categorize**.
3. Choose up to 25 rows in **Rows to ask Jev about**, check the disclosure consent,
   and click **Ask Jev for suggestions**. This selection is independent of the
   existing posting-inclusion checkboxes.
4. Review the outcome. An account suggestion must be accepted explicitly to fill
   the category control. Insufficient information, split required, and transfer
   review outcomes leave categories alone and call for human work. Split creation
   is outside this integration; resolve it through the existing accounting workflow.
5. Review inclusion and category/transfer controls before posting normally.

**Off (local rules only)** is the default until a provider is saved, including
for existing installations with an Anthropic key. Existing Anthropic categorization
remains selectable, with its existing behavior. Document parsing and Book Review
retain their separate Anthropic behavior; this setting controls import categorization.
No provider call occurs from loading the page, choosing rows, changing consent,
sorting, accepting a suggestion, or ordinary reruns. Offline manual review and
local pattern matching remain available.

## Data and boundaries

The allowlisted request contains dates, descriptions, integer-cent amounts,
source account IDs, transfer flags, receipt text if supplied, client entity/business
type and optional AI business context, and eligible account IDs/numbers/names/types/
subtypes. General client Notes, filenames, fingerprints, idempotency keys, ledger
paths, and credentials are not part of model state. There is no receipt uploader
added by this change; absent receipt evidence is sent as empty text.

Eligible account choices are active Expense/Revenue accounts owned by the selected
client. Other accounting situations may need transfer review or insufficient
information. Account choices are supplied by code and response IDs must match
those choices exactly. The adapter validates question IDs, answer type, complete
finite probability distributions, totals, winner, and concentration. A malformed
batch fails closed, leaving staged transactions unchanged.

Live Jev 1.13 also returns two-decimal probabilities whose total can be 0.99 or
1.01. Validation accepts only a rounding-compatible discrepancy bounded by 0.005
per choice and capped at 0.02 overall; non-rounded discrepancies retain the 0.001
normalization tolerance. Original values are preserved. Materially wrong totals,
unknown/missing choices, nonfinite values and an invalid winner still fail closed.

`services/jev_categorization.py` has no database-write or posting path. It returns
review outcomes; `utils/jev_review.py` displays them inside the existing import
page. Only a human acceptance fills a category. It never changes posting inclusion
or the transfer toggle, learns a pattern, creates an account, or posts an entry.
Existing SQLCipher connections, balanced-entry validation, integer cents, audit
actor stamping, staged-row adoption, fingerprints and permission authorizers remain
the owners of those invariants. Human posting continues to generate its normal audit
trail. Unaccepted suggestions are transient, not durable accounting records.

Jev confidence is **distribution concentration**, not a correctness probability
or authorization to post. Even concentration 1.0 requires human review.

## Request reuse and errors

A cache belongs to the Streamlit session and is cleared on book/client switching.
Its SHA-256 keys include the local ledger path, client ID, all disclosed evidence,
all eligible accounts, business context, model identifier, instructions and outcome
criteria. Ledger identifiers stay local. Sorting and posting inclusion do not alter
inference inputs. Changed evidence/accounts/context/instructions produce a different
key, so stale suggestions cannot be accepted. A previously accepted Jev category
is cleared when those inputs change if the reviewer has not selected a different
category, including when the provider has since been turned off. Accepted and
manually selected categories survive leaving and returning to the review view;
an explicit clear does not resurrect an older suggestion. Existing
manual/deterministic categories are otherwise preserved.

Successful results are reused for identical inputs, even on a repeated request click.
Failures and interrupted requests are remembered too. A pending marker is installed
before network IO. **Retry failed Jev requests** is an explicit action, with a warning
that retrying after a timeout may charge again. There is no automatic retry or
provider fallback. A 30-second socket timeout bounds stalled network operations;
responses are capped at 2 MB. Redirects are rejected, and provider exception bodies
are never shown or stored. Missing keys, invalid keys, rate limits, network failures
and invalid responses leave staged work available for manual review.

The request key includes the actual Choice criteria, so editing an option's rubric
also invalidates reuse. Confirmed mixed components, unresolved conflicting evidence,
ordinary card purchases and card-balance payments have distinct review rules.
Instructions embedded in transaction/receipt/account/context text cannot authorize
an action and are explicitly excluded as evidence of a business purpose. These are
model instructions, not a guarantee against semantic errors; human review remains
mandatory.

Selecting up to 25 rows may require multiple requests for a large chart or long
receipts. The panel discloses the new request count. Conservative UTF-8 JSON byte
budgets (60,000 overall and 30,000 for state plus the largest question) stay below
the documented 64k/32k token limits without adding a tokenizer. No evidence or
eligible accounts are silently truncated. An individually oversized row stays local
with guidance; unchanged retries do not send it. All pending keys are marked before
network IO, and a transport failure stops the remaining requests until explicit retry.
Validation failures remain isolated to the affected response batch.

Reuse lasts only within the current session; restarting or opening a second session
requires another explicit request and may charge again. `jev-latest` can move upstream;
recorded responses include the actual model. There is no cross-session paid-request
idempotency claim or persistent cache of financial evidence.

## Tests and desktop packaging

```sh
# On Charlie's Mac, from LedgerLabs/ProBooks:
.macos-venv/bin/python -m pytest -q -m 'not performance'
.macos-venv/bin/python -m pytest -q tests/test_jev_categorization.py tests/test_jev_review.py
.macos-venv/bin/python scripts/compare_jev.py

# Explicit live request: synthetic fixtures only; the key is never printed.
.macos-venv/bin/python scripts/compare_jev.py --live-jev \
  --key-file ~/.typesafe.env --output output/jev-comparison.json

# Optional Anthropic comparison: supply ANTHROPIC_API_KEY in the environment.
.macos-venv/bin/python scripts/compare_jev.py --live-jev --live-anthropic \
  --output output/jev-comparison.json

# Real browser, fake provider/keychain, disposable encrypted book:
.macos-venv/bin/python scripts/jev_browser_fixture.py --port 8629

# Automated browser regression checks (requires agent-browser and Chromium):
.macos-venv/bin/python scripts/check_jev_browser.py
```

Open `http://127.0.0.1:8629` for the last command. Pick the synthetic row, consent,
request twice, and confirm **Synthetic transport calls: 1**. Accept and verify the
category fills while the posting checkbox stays unchecked. **Change synthetic
evidence** must clear the accepted category without a new call. Enable **Simulate
network timeout** and request: the row remains, repeat requests reuse the failure,
and only **Retry failed Jev requests** increases the call count. Stop the launcher
when finished. It replaces credential storage and network transport only inside
its own process. Never point tests at a real ledger.

The automated browser script starts and stops this fixture itself. Its default
browser CLI is `tests/browser-tools/node_modules/.bin/agent-browser`; override with
`--agent-browser /path/to/agent-browser`. It writes a transcript, screenshot and
result under `output/jev-browser-review` (override with `--output`).

Automated tests use the existing fake-vault and disposable SQLCipher fixtures.
Coverage includes consent, provider/key storage, reruns, account acceptance,
invalidation, explicit retries, malformed distributions/invented IDs, redirects,
request interruption, and staged-row identity through normal human posting.
Read/propose posting attempts remain denied; human posting produces balanced
3,333-cent debit/credit lines, preserves the staged ID, is audit-attributed to the
human, and reimport remains idempotent.

There is no TypeSafe SDK dependency. HTTP uses Python's standard library plus
`certifi`, which was already pinned at 2026.7.22 in both desktop locks and is now
an explicit runtime requirement. TLS uses its bundled CA file, not the build
machine's OpenSSL certificate path. `LedgerTB.spec` collects `certifi` and explicitly
includes the HTTP modules; its existing service/utils data directories include the
new modules. The desktop selfcheck imports both modules and `certifi`.

Verified locally after review: full non-performance suite (775 passed, 5 skipped,
2 performance tests deselected), focused
Jev/identity/state checks, `pip check`, source selfcheck (44 imports plus Apple Vision
OCR, fake credential backend), and the real browser flow above. The sandboxed OCR
selfcheck failed; the same check passed outside the sandbox.

The final focused run passed 50 tests. The automated Chromium run passed eight
checks: consent, separate posting inclusion, acceptance, paid-request reuse,
navigation persistence, provider-off invalidation, failure preservation and immediate
explicit retry. Browser automation uses Tab to leave the multiselect popover;
Escape can interrupt Streamlit, and its accessibility-only Dismiss button cannot
be clicked normally. The final transcript and screenshot are in
`output/jev-review-20260917/browser/`.

The review reproduced and fixed three state bugs: accepted categories disappearing
after leaving Review; stale accepted categories surviving input changes after Jev
was disabled; and the retry button staying disabled immediately after the first
failure. Each regression test failed before the fix. Tests also cover subsequent
manual overrides, explicit category clears, and context changes while Review is
unmounted.

An isolated Mac PyInstaller bundle built successfully and passed its 44-module
and Apple Vision OCR selfcheck. Packaged Jev modules were compared with source;
the packaged `certifi/cacert.pem` loaded with certificate and hostname verification
enabled. The selfcheck used a synthetic API key and a disposable path and did not
open a ledger or read credential secrets. Build/test logs and the ad-hoc-signed
bundle are under `output/jev-review-20260917/`. This checks Mac packaging/imports
and CA availability, not packaged live-network or installed-app acceptance.
Windows packaging remains unverified. No build was installed or published.

## Labeled synthetic comparison

The expanded 120-case protocol and development/held-out separation are specified in
[JEV-VALIDATION-PLAN.md](JEV-VALIDATION-PLAN.md). The original smoke results below
remain historical evidence; they do not establish the expanded set's accuracy.

The fixed [fixture](../tests/fixtures/jev_comparison.json) labels 16 fictional cases:
clear controls, ambiguous merchants, transfers/card payments/owner funding,
refunds, mixed purchases, and missing receipts. The local baseline is the existing
`PatternLearner.find_match`, with four explicitly supplied training patterns; an
unmatched local row is scored as insufficient information. The comparison deliberately
includes merchant reuse with conflicting evidence. This is a small smoke test with
clear labels, not a representative production dataset or a calibrated accuracy study.

Final transport run, September 17, 2026:

| Provider | Correct labeled outcomes | Abstentions/review outcomes | Confident errors | Latency |
| --- | ---: | ---: | ---: | --- |
| Existing local patterns | 7/16 (43.75%) | 8/16 | 4 | median 0.00057 s/row |
| Jev 1.13.0 | 16/16 | 11/16 | 0 | 0.922 s for one batch of 16 |
| Existing Anthropic service | Not run | — | — | Development key not supplied |

Jev made five account suggestions and chose six insufficient-information, two
split-required, and three transfer-review outcomes. All still require review.
Correctness counts exact agreement with the labeled outcome; appropriate abstention
counts as correct. Service errors are separate. A confident error is a wrong outcome
with concentration/confidence >= 0.8; local rule confidence and Jev concentration are
not calibrated equivalents. Batch latency is repeated in Jev's per-case records;
do not sum those values as 16 separate requests.

The final response reported **10,836 input tokens and 2,605 output tokens**.
[TypeSafe's model documentation](https://docs.typesafe.ai/models), checked September
17, lists Jev 1.13 at $0.042 per million input tokens, with output tokens free.
That yields an **estimated $0.000455112 for this batch**; actual billed cost was not
returned by the API or independently checked. The earlier transport smoke request
used 10,801 input and 2,589 output tokens and also matched 16/16, taking 0.738 seconds.
The two live calls together used 21,637 input tokens (estimated $0.000908754).
Raw final outcomes, distributions and usage: [jev-comparison-results.json](jev-comparison-results.json).

Before rollout: commit the reviewed source; run an approved Anthropic side-by-side if
wanted; expand labels to less explicit, representative fixtures and review errors;
confirm the firm's TypeSafe data-handling terms; verify packaged live TLS and review
behavior on Mac, and packaging/imports/TLS/review on Windows; then obtain separate
release/install approval.
The existing Windows native save/upgrade acceptance and broader import workbench
threads remain open in Notion. This change does not implement that workbench redesign.

## API references

Implementation follows the current [HTTP API](https://docs.typesafe.ai/api),
[Choice primitive](https://docs.typesafe.ai/primitives/choice),
[state guidance](https://docs.typesafe.ai/concepts/state), and
[confidence guidance](https://docs.typesafe.ai/confidence), discovered through
[llms.txt](https://docs.typesafe.ai/llms.txt). The
[Choice consistency cookbook](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook)
informed the explicit abstention route; its example thresholds were not adopted
as permission to act.
