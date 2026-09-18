# Optional TypeSafe Jev categorization

Implementation and local verification: 2026-09-17–18. Current business priorities and
rollout decisions live on the [LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
This is a source implementation, not a released or installed application update.

## Use

1. In **Firm Settings → AI categorization**, select **TypeSafe Jev**, save the
   provider, and save your own TypeSafe key. Keys use `utils/secure_store.py` and
   the OS credential vault. The desktop app never reads `~/.typesafe.env`.
2. Load staged transactions through **Import Transactions → Review & Categorize**.
3. Open **Select rows for actions** and choose rows in **Selected rows**. Open
   **AI suggestions**, check the disclosure consent, and click **Ask Jev for suggestions**.
   Jev accepts at most 25 selected rows at a time. Selection is shared with manual
   bulk category changes and remains independent of posting-inclusion checkboxes.
4. Review the outcome beside its transaction. An account suggestion must be accepted explicitly to fill
   the category control. Insufficient information, split required, and transfer
   review outcomes leave categories alone and call for human work. Split creation
   is outside this integration; resolve it through the existing accounting workflow.
5. Review inclusion and category/transfer controls before posting normally.

Review rows are shown 50 at a time. **Include All / Exclude All** controls posting
across the whole review list. **Select rows for actions** controls the shared action
selection: applying an account or clearing that selection never changes posting
inclusion. Bulk transfer edits require an asset/liability account. Categories,
transfer flags and exclusions remain attached to stable row IDs when paging or
sorting. Posting uses included rows on every page, as disclosed above the grid.
Summary counts also cover all pages: empty suggestions remain uncategorized,
and parked categories stay visible in the review count when their rows are
off-screen. Accepting a Jev account updates the summary in the same interaction.

**Off (local rules only)** is the default until a provider is saved, including
for existing installations with an Anthropic key. Existing Anthropic categorization
remains selectable, with its existing behavior. Document parsing and Book Review
retain their separate Anthropic behavior; this setting controls import categorization.
No provider call occurs from loading the page, choosing rows, opening action panels, changing consent,
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

For large imports, request inputs are built only for selected, previously requested,
or accepted rows. Previously requested results remain available after leaving and
returning to Review; accepted suggestions are still checked for stale evidence even
with Jev off. The grid reuses loaded account labels instead of querying each row's
source account separately.

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

The expanded 120-case comparison, first held-out result and iteration failures are
documented in [the September 17 evaluation report](JEV-EVALUATION-2026-09-17.md).

`LEDGERTB_DATA_DIR` can point a source or frozen launch at an absolute, isolated
data home (registry, default book, backups and legacy-key-file location). Without
the override, existing directory selection is unchanged. It does not isolate the
OS credential vault: acceptance fixtures must also supply their test-only keyring
backend. `scripts/check_packaged_jev.py` requires a disposable `.app` under this
checkout's `output/`, verifies production source hashes, adds only the test fake
vault module, and creates/removes its own fictional encrypted books. It never
replaces the installed application. Adding the test backend changes the bundle's
resource seal, so the harness ad-hoc signs and verifies that disposable bundle
before launching it. It uses no developer signing certificate or real keychain.
Example after building there:

```sh
PYINSTALLER_CONFIG_DIR="$PWD/output/jev-package-cache" \
  LEDGERTB_CODESIGN_ID= PROBOOKS_CODESIGN_ID= \
  .macos-venv/bin/python -m PyInstaller LedgerTB.spec --noconfirm \
  --distpath output/jev-overnight-20260917/dist \
  --workpath output/jev-overnight-20260917/build
.macos-venv/bin/python scripts/check_packaged_jev.py \
  --app output/jev-overnight-20260917/dist/LedgerTB.app \
  --output output/jev-overnight-20260917/packaged-browser
# Optional explicit fictional live TLS check: add --key-file ~/.typesafe.env.
# The key is read in memory by the test backend, never copied into the bundle.
```

For the manual `jev_browser_fixture.py --port 8629` command above, open
`http://127.0.0.1:8629`. Pick the synthetic row, consent,
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

Initial review baseline, before the overnight additions: full non-performance suite (775 passed, 5 skipped,
2 performance tests deselected), focused
Jev/identity/state checks, `pip check`, source selfcheck (44 imports plus Apple Vision
OCR, fake credential backend), and the real browser flow above. The sandboxed OCR
selfcheck failed; the same check passed outside the sandbox.

The initial review's focused run passed 50 tests. The automated Chromium run passed eight
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

### September 18 packaged workflow acceptance

The fresh isolated Mac bundle passed ten end-to-end checks through its frozen
server in Chromium, using the test-only fake vault and disposable encrypted books.
The [saved result](jev-evaluation-results/packaged-mac-2026-09-18.json) records the
production source hashes and transport evidence. After importing two fictional
CSV rows and excluding both, a simulated offline request left both staged rows
intact. Asking again reused the failure. Explicit Retry made exactly one real
TLS request to TypeSafe; accepting its account suggestion and asking again reused
that result without another network request. Bulk select/clear preserved exclusion.

The reviewer then included only the accepted row and posted it through the normal
page. Database inspection found one imported transaction and one journal entry,
with 3,333-cent debit and credit lines, preserved source/fingerprint/idempotency
fields, and human attribution. The encrypted file header and audit records were
verified. The final screenshot reads “Posted 1 transaction — 1 excluded.” Jev
1.13.0 reported 1,229 input / 131 output tokens. This one acceptance request is
additional to the evaluation totals; billed dollars were not available.

Evidence is under `output/jev-overnight-20260917/packaged-browser-tooltip/`:
`result.json`, `transport-events.jsonl`, `database-evidence.json`, `browser.json`,
`posted.png`, and signature/server logs. Earlier attempts are retained separately:
startup/readiness/fixture subprocess timeouts occurred under heavy host load;
another attempt exposed the Retry hover tooltip covering Ask after scrolling.
The harness now moves the pointer away and checks the actual hit target before
ordinary clicks, captures screenshots on failure, and retries only read-only
waits. No failed attempt made a live provider request. No forced click or replayed
mutation was used to obtain the passing run.

This establishes packaged Mac imports, live CA/TLS, offline recovery, rerun reuse,
human review and posting for this fictional case. It does not establish native
window, installed-upgrade or Windows acceptance, production model accuracy, or
startup/performance suitability under normal load. Final broad regression and
volume rechecks remain separate gates.

After the summary fixes in `87aa093`, the rebuilt bundle passed an expanded
[12-check workflow](jev-evaluation-results/packaged-mac-final-2026-09-18.json)
with a fake provider. In addition to the posting/reuse/recovery checks, it switched
to another encrypted book with the same client ID, account IDs, business context
and transaction evidence. Consent and previous review state were cleared; choosing
the same row required fresh consent/request and did not expose the first book's
result. [Database inspection](jev-evaluation-results/packaged-book-switch-2026-09-18.json)
confirmed one approved entry/import in the first book and none in the second.
No real provider request was made in this rerun. The adapter and CA setup are
unchanged from the earlier successful live-TLS check; the result files retain
their respective source hashes rather than implying they tested identical UI code.

This final-source evidence is under
`output/jev-overnight-20260917/packaged-browser-short-namespace/`, including both
posting and book-switch screenshots. The harness uses a short, unique browser
daemon namespace, a separate session and a pinned tab. Earlier attempts retain
an unexpected transition to `about:blank` and a macOS socket-path-length error;
neither is counted as a passing application check.

### September 18 final regression and volume checks

The final normal functional suite passed **802 tests**, with five Windows-only
NTFS skips, in 201.10 seconds. [The verification record](jev-evaluation-results/final-functional-tests-2026-09-18.json)
also preserves an earlier full run's five timeouts and their targeted reruns.
The clean full run used original deadlines and assertions after host contention
subsided; it did not use the longer Streamlit waits from earlier diagnostic runs.

All three performance tests passed serially with their original thresholds;
[recorded metrics](jev-evaluation-results/performance-2026-09-18.json) retain the
source commit and qualifications. The 10,000-row review rendered 50 row controls
across 200 available pages in 0.599 seconds, preserved exclusions, and performed
no cloud-input preparation or provider calls. This is AppTest server rendering,
not browser paint or desktop startup timing.

The 50,000-row CSV parsed in 8.3349 seconds and classified duplicates in 4.0641
seconds, preserving the nine expected duplicates and every import identity at
45.05 MiB peak traced memory. The 10,000-entry journal fixture passed its query,
audit and page-render thresholds too. These are single synthetic measurements;
earlier heavily contended runs (141-second review rendering and failed CSV timing
limits) remain in `output/jev-overnight-20260917/` and are not counted as passes.

The final source Chromium fixture also passed all eight consent, inclusion,
acceptance, reuse, navigation, invalidation and recovery checks. Its
[result](jev-evaluation-results/source-browser-final-2026-09-18.json) uses the
same isolated browser namespace and pinned tab as the packaged harness.

### September 18 native startup and self-check limits

The final disposable bundle opened a native LedgerTB window with the expected
passphrase unlock screen. The window's loopback port matched its isolated fixture
server. This checks native startup only; unlock and the full workflow were verified
through the packaged Chromium checks above, not through this native window.
Only the identified test launcher and its child were stopped, and their exit was
verified. No installed application was replaced or stopped.

The final bundle's `--selfcheck` exited 1 because its release gate correctly
rejected `packaged_fake_vault.Keyring` as the macOS backend. Its only reported
failure was that backend identity check: all 44 imports, required SQLCipher and
the real Apple Vision OCR recognition check completed without reported failure.
This is **not a passing release self-check** or a real keychain acceptance test.
The production gate was not weakened. The earlier bundle's passing self-check
remains historical evidence for that earlier build. See the
[scoped native and self-check record](jev-evaluation-results/native-mac-2026-09-18.json).

## September 18 walkthrough usability pass

Charlie verified the simulated native workflow: accept an Office Supplies
suggestion, retain both exclusions, include only Cedar Paper, and post one
balanced $33.33 entry while leaving the unknown purchase unposted. He then
requested a focused import/journal usability pass based on the crowded screens.

The upload preview now leads to **Check totals → Continue to review**. Column
mapping, sign convention and saved-format controls are grouped under **Import
settings** (opened by default when detection is incomplete). A changed file,
account, mapping or sign interpretation clears the previous confirmation.

Review uses a compact toolbar: shared row selection, AI suggestions, manual
category changes and sorting. The disclosure and consent remain inside the
explicit AI action panel. Cached suggestions and errors appear beside their
transaction's category. The single-page pagination selector is omitted; larger
imports retain the existing 50-row paging and across-page inclusion behavior.
Selecting more than 25 rows remains valid for manual bulk categorization but
blocks a Jev request without silently truncating or changing the selection.

Journal filters are collapsed, and entry lines use an Account / Debit / Credit /
Memo table with numeric amounts and a cent-based balance indicator. **Change
category** retains the existing imported-entry correction workflow.

The screenshot's zero upload totals did not reproduce in the ordinary source
fixture. The preview's separate coercion pipeline was replaced with the existing
import amount parser, adding separate debit/credit support, cent-based sums and
an explicit unavailable-total warning for unreadable amounts. The rebuilt Mac
bundle visibly reports $81.58 disbursements and -$81.58 net for the two sample
rows. This verifies the corrected path without claiming a proven cause for the
original frozen-only symptom.

Verification logs and screenshots are under `output/ux-polish-20260918/`.
The normal full suite passed **810 tests**, with five Windows-only skips, in
150.74 seconds. A final journal-only empty-cell display adjustment was followed
by **41 passing journal-page tests** and all 14 final-source packaged checks; the
full-suite result precedes that display-only adjustment. All **three performance
tests** passed with original thresholds:
10,000-row review 0.633 seconds; 50,000-row CSV parse 6.1739 seconds, duplicate
classification 2.2489 seconds, peak 44.92 MiB. The source browser passed all eight
state/recovery checks. Tests use fake vaults/providers and disposable encrypted
books; this pass made no real TypeSafe requests. Packaged screenshots and a
machine-readable [acceptance result](jev-evaluation-results/usability-2026-09-18.json)
record all 14 passing packaged checks, including totals and journal display.
Earlier harness visibility/selector failures remain in the output directory.
This remains a local test build; native upgrade, release credential-vault and
Windows acceptance gates are unchanged.

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

The reviewed source and expanded evaluation are saved on the local
`codex/jev-overnight` branch. Before rollout: independently adjudicate representative
labels; run an approved Anthropic side-by-side if wanted; confirm the firm's
TypeSafe data-handling terms; complete clean release-bundle/native credential-vault
and installed-upgrade acceptance on Mac, plus packaging/imports/TLS/review and
native acceptance on Windows; then obtain separate release/install approval.
Mac packaged live TLS and the final-source staged review/posting workflow have
the distinct passing evidence described above.
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
