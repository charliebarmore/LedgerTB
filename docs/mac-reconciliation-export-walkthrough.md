---
cursor:
  subagentId: "bc-20a10511-1c16-5371-b7a4-e8b504859d25"
---

# LedgerTB v1.8.0 — Mac reconciliation and export walkthrough (release-readiness §4a)

Test method: the reconciliation and export run was the packaged binary in server mode driven by headless Playwright, plus a separate real Cocoa NSSavePanel check — not a complete native-window walkthrough.

Executed 2026-09-22 05:06–05:23 EDT on Charlie's MacBook Air (macOS 26.6.2), user `charliebarmore`.

**Result: PASS — all seven §4a steps met their pass criteria on the qualified build. No blockers. Two non-blocking observations (§6).**

## 1. Build under test

| Item | Value |
| --- | --- |
| Bundle | `~/LedgerLabs/ProBooks/output/release-candidate-20260920/dist-final/LedgerTB.app` |
| Version | `CFBundleShortVersionString` 1.8.0, `CFBundleVersion` 1.8.0, bundled `version.py` `APP_VERSION = "1.8.0"` |
| Main executable SHA-256 | `14520c9a43d3073d33ce6b762aa36792ef3328ddf50b98e9701451ee142dd756` (`Contents/MacOS/LedgerTB`) — byte-identical to `archive-verification/LedgerTB.app` (the copy re-extracted from the release zip); `diff -rq` of the two bundles reports no content differences |
| Release zip SHA-256 | `7bb1bfe143f057ebdbe80c33970ba3b440917ec7166513743f55148586748ef2` — matches `artifact-manifest.json` and `SHA256SUMS` |
| Source commit (manifest) | `a351c3ebe4ad0f837193c1720e56929beecc3539` |
| Signature | `codesign --verify --deep --strict` OK; Authority `Developer ID Application: Ledger Labs LLC (TYU6FU47HR)`; timestamp Sep 20, 2026 3:19:23 PM; `Notarization Ticket=stapled`; `xcrun stapler validate` OK; `spctl --assess --type execute` → `accepted`, `source=Notarized Developer ID` |
| After the run | `codesign --verify --deep --strict` still passes; executable hash unchanged; no file inside the bundle was modified during the run (frozen build sets `dont_write_bytecode`) |

Not touched: `/Applications/LedgerTB.app` (still v1.7.2), the `~/LedgerLabs/ProBooks` checkout (still dirty, still on `codex/jev-overnight`, nothing committed), any real client book.

## 2. How it was driven

GUI automation of the native window was not available from this session (`osascript`/System Events timed out — no Accessibility grant; `screencapture` returns "could not create image from display" — no Screen Recording grant). So, per the fallback in the assignment:

- **Primary path — qualified binary in server mode + Playwright.** `dist-final/LedgerTB.app/Contents/MacOS/LedgerTB` was launched directly with `LEDGERTB_MODE=server`, `LEDGERTB_PORT=58628`, a random `LEDGERTB_UI_TOKEN`, `LEDGERTB_DATA_DIR=/tmp/ledgertb-recon-walkthrough-20260922/data`, `PYTHON_DOTENV_DISABLED=1`, and `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `TYPESAFE_API_KEY` each set to a fake unused value (so no provider call or keychain read for an API key could occur).
  Headless Chromium (Playwright 1.61.0 from `/opt/anaconda3`) drove the UI at `http://127.0.0.1:58628/...?t=<token>`; the token gate was honoured (no "This page was not opened by LedgerTB" refusal). Every step's DOM text, metric values, download events and screenshots were recorded (`walk-log.jsonl`).
- **Supplement — real native `NSSavePanel` against the same qualified server.** Because a browser cannot show the app's native save panel, a small pywebview shim (`native_panel_check.py`, run with the repo's pinned `.macos-venv` — the same pywebview build PyInstaller bundled, with the launcher's `ALLOW_DOWNLOADS`/`OPEN_EXTERNAL_LINKS_IN_BROWSER` settings) opened a Cocoa/WebKit window on the same server and clicked **Export Excel**. This exercised the real modal `NSSavePanel` for both Cancel and Save. Not the bundle's own parent-process code, but the identical library and settings; the server side (all application logic) was the qualified binary throughout.
- `scripts/native_pilot_driver.py` was not used: it runs the app **from source** (`import run_ledgertb` at the checkout root), which would not test the qualified bundle.

Fixture: a brand-new encrypted book created through the first-run gate in a temp directory, with a throwaway passphrase, not recorded here, **"Remember on this computer" left unticked** (verified unchecked before submit). Fictional client **Harbor Lantern Design Studio LLC** (S-Corporation / Professional Services, default chart seeded). One regular journal entry #1 dated 2026-09-01, "Pinecone Print Co. printer paper": 6200 Office Supplies debit 33.33 / 1000 Cash - Operating credit 33.33. (The default chart names the cash account "Cash - Operating", not "Checking" as in the §4a script; the amounts and tie-outs are as scripted.)

## 3. Step results

| § | Step | Result | Evidence |
| --- | --- | --- | --- |
| 0 | Verify qualified bundle hash/signature | PASS | §1 |
| 1 | Bank Reconciliation → Cash - Operating; start reconciliation, statement ending balance **-33.33**, end date 2026-09-22 (after the 09-01 entry) | PASS | Draft created; audit `Created · bank_reconciliations #1 05:15:35` |
| 2 | Header shows Difference **-$33.33**; tick **Cleared** on the sole line (canvas checkbox) → **Save cleared items** | PASS | Metric text `Difference $-33.33`, warning "Select the entries that cleared…" shown; glide cell `glide-cell-0-0` went `false → true` after a real pointer click on the canvas; Screenshots are in the project evidence archive: `mac-recon/03-recon-difference-minus-33-33.png`, `mac-recon/04-recon-cleared-ticked.png`. |
| 3 | Difference **$0.00**, warning gone; tick confirmation → **Complete reconciliation**; history shows **Completed** | PASS | Metric `Difference $0.00`, `Cleared ledger balance $-33.33`, green "Reconciled — the cleared ledger balance matches the statement."; history row `2026-01-01 to 2026-09-22 · -33.33 · Completed · 2026-09-22 09:18`; audit `Updated · bank_reconciliations #1 05:17:03`, `Closed · bank_reconciliations #1 05:18:03`; Screenshots are in the project evidence archive: `mac-recon/05-recon-difference-0-00.png`, `mac-recon/06-recon-history-completed.png`. |
| 4 | Trial Balance Worksheet → **Export Excel** → **Cancel** the save; app responsive, no file, no error banner | PASS (two ways) | (a) Real `NSSavePanel`: panel appeared modal (`kind NSSavePanel, modal=true`), `cancel:` sent, panel closed, downloads folder unchanged (`[] → []`), Export Excel button still present, no error alert. (b) Browser: Playwright download cancelled (`failure = "canceled"`), no file, **Refresh** rerun completed, buttons present, no error banner; Screenshot is in the project evidence archive: `mac-recon/07-export-excel-cancelled-app-responsive.png`. |
| 5 | **Export Excel**, **Close Package (PDF)**, **Close Package (Excel)** → save all three | PASS | Saved to the disposable downloads folder: `TB_Worksheet_…_20261231.xlsx` 5,660 B; `ClosePackage_…_20261231.pdf` 11,013 B (8 pages); `ClosePackage_…_20261231.xlsx` 14,002 B. Native-panel Save also produced a valid `TB_Worksheet_…xlsx` (5,661 B) — see §6.2; Screenshots are in the project evidence archive: `mac-recon/08-export-excel-saved.png`, `mac-recon/09-close-package-pdf-saved.png`, `mac-recon/10-close-package-excel-saved.png`. |
| 6 | Open each file: fictional client name in header, balanced **33.33/33.33**, Office Supplies 33.33 / Cash (33.33), no tie-out mismatch, dates rendered (no `%-d` artifacts) | PASS | §4 |
| 7 | Audit Trail: reconciliation completion and exports stamped with the OS user, no "(AI)" | PASS | 27 entries, every one `By: Charlie Barmore`; actions `INSERT/UPDATE/CLOSE/EXPORT`; four `EXPORT` rows (05:18:28 cancelled Excel, 05:18:31 Excel, 05:18:32 PDF, 05:18:33 close-package Excel); no "(AI)" anywhere; Screenshot is in the project evidence archive: `mac-recon/11-audit-trail.png`. Full text in `internal/mac-recon-evidence/audit-trail.txt` |

Setup evidence: Screenshots are in the project evidence archive: `mac-recon/00-first-run-passphrase-remember-off.png`, `mac-recon/01-fictional-client-created.png`, `mac-recon/02-journal-entry-33-33.png`.

## 4. Export file validation (opened with openpyxl 3.1.5 / pdfplumber)

SHA-256 of the files tested:

```
ccd351d92ca8cef75be53d21c4ca4e9c5f7ed075db91d56f0dcdfd9d00e60e53  TB_Worksheet_Harbor Lantern Design Studio LLC_20261231.xlsx
2a356c7b9aebaf63ebd40eecaab4526dc2ce062aaa5f7b134b5ea564e6bdb0e3  ClosePackage_Harbor Lantern Design Studio LLC_20261231.pdf
4d168bf3dc716b2a11b70f6502ae65997092c42534ec67e62822a4a2e20eb41f  ClosePackage_Harbor Lantern Design Studio LLC_20261231.xlsx
e4f85cbfdff44c4182fde30278a31aa4c80be792dc5f39ded1e1fe298325b0b2  TB_Worksheet_…_20261231.native-save.xlsx  (saved through the real NSSavePanel)
```

- **TB Worksheet (Excel)** — one sheet "Trial Balance Worksheet". A1 `Trial Balance Worksheet - Harbor Lantern Design Studio LLC`, A2 `Period: 01/01/2026 - 12/31/2026`, A3 `Generated: 09/22/2026 05:18`. Row 6: 1000 Cash - Operating, Credits (G6) 33.33; row 7: 6200 Office Supplies, Debits (F7) 33.33; Unadj/Adj TB columns are live `=MAX(...)` formulas, TOTALS row `=SUM(...)` per column (by design — the workbook carries formulas). Opens cleanly.
- **Close Package (Excel)** — 9 sheets: Summary, Income Statement, Balance Sheet, Trial Balance, Close Map, Transactions, Adjusting Entries, Receipts & Disbursements, Cash Flow. Summary: client name; `Final trial balance - total debits 33.33`, `total credits 33.33`, `In balance YES`, `Balance sheet in balance YES`, `Net income ties to balance sheet earnings YES`, `Cash flow status READY`. Trial Balance sheet: 1000 Cash - Operating Activity Cr 33.33 / Final Cr 33.33; 6200 Office Supplies Activity Dr 33.33 / Final Dr 33.33; TOTALS 33.33/33.33. Transactions sheet shows both lines of entry #1 dated 2026-09-01.
- **Close Package (PDF)** — 8 pages, Title `Close Package - Harbor Lantern Design Studio LLC`, Author `LedgerTB`, ReportLab. Page 1 header `Harbor Lantern Design Studio LLC / Close Package / January 1, 2026 to December 31, 2026 / Generated September 22, 2026 at 5:18 AM`; summary `total debits 33.33 / total credits 33.33 / In balance Yes / Net income ties to balance sheet earnings YES`. Income Statement `6200 - Office Supplies 33.33`, `NET INCOME (33.33)`; Balance Sheet `1000 - Cash - Operating (33.33)`, "Balance sheet is in balance."; Final Trial Balance TOTALS `33.33 33.33 33.33 33.33`; Transactions page lists the two lines. All dates render as full words or ISO — no `%-d`/`%-I` artifacts anywhere in the extracted text.
- No "Reconciled" mismatch appears on any tie-out; every tie-out reads YES / in balance / READY.

## 5. Cleanup and side-effect checks

- **Processes:** the server I started (pid 28385) was stopped with SIGTERM ("Stopping..." in its log) and is gone; no Playwright/Chromium or pywebview processes remain. Three **pre-existing** LedgerTB processes from 2026-09-19 (pid 96986 `output/usability-overnight-20260918/dist/LedgerTB.app`, and pids 22125/74804 running `.macos-venv/bin/python run_ledgertb.py`) were already running before this walkthrough and were left alone — they are not mine and are unrelated to the qualified build.
- **Keychain:** nothing created, nothing removed. "Remember on this computer" was never ticked; `security dump-keychain` shows the same 36 `com.ledgerlabs.*` item matches before and after, and the same set of `book_key_*` accounts. The app's only vault reads were the per-book remembered-key lookup (no such item) — the API-key read was short-circuited by the fake env vars.
- **Stray files:** the native-panel Save landed in `~/Downloads/TB_Worksheet_Harbor Lantern Design Studio LLC_20261231.xlsx` (the panel's default location — see §6.2); I moved it into the disposable folder. `~/Downloads` now contains nothing from this run. The two older `ClosePackage_Cedar Demo Services_*` files there date from Sep 7 and are not mine.
- **Throwaway book:** `/tmp/ledgertb-recon-walkthrough-20260922/data/` (encrypted `accounting.db`, `books.json`); the book's one-writer lock was released on shutdown. The whole `/tmp/ledgertb-recon-walkthrough-20260922/` tree can be deleted; durable copies of the evidence are in the project evidence archive (§7).
- **Repo:** `git status` unchanged (`M CLAUDE.md`, untracked `.worktrees/ output/ tmp/`), branch `codex/jev-overnight`, nothing committed or checked out. Nothing written under `output/release-candidate-20260920/` (the §4a "Log it" doc edits to `RELEASE-REVIEW-1.8.0.md` were deliberately not made — that would modify the checkout).

## 6. Observations (none blocking)

1. **A cancelled save still writes an EXPORT audit row.** `st.download_button(on_click=AuditLog.log_event)` logs at click time, before the native panel opens, so the cancelled Export Excel produced `Exported · trial_balance_worksheet_export 05:18:28` with no file on disk. Repro: Trial Balance Worksheet → Export Excel → Cancel → Audit Trail. Arguably correct (the workbook was generated and offered), but the audit trail cannot distinguish a cancelled save from a completed one. Cosmetic; note for the release review.
2. **Native Save: file, destination.** Through the real `NSSavePanel`, Save wrote a valid workbook, but it went to the panel's default `~/Downloads` even though the shim had re-pointed the panel's directory/name field before ending the modal session with OK. That is a limit of programmatic panel control (the panel keeps its original URL when the session is ended without a user confirm), not an app defect — a human choosing a folder in the panel is the case a human tester should still eyeball once. The Cancel branch was fully genuine.
3. **Reconciliation history "Completed" column shows UTC** (`2026-09-22 09:18` for a 05:18 EDT completion) while the Audit Trail shows local time (`05:18:03`). Cosmetic inconsistency; not in the §4a criteria.
4. Fresh-book quirk of the fixture, not the app: with only a 33.33 payment posted, Cash - Operating is (33.33) and Total Assets (33.33); all statements still tie.

## 7. Files produced

- This report: `docs/mac-reconciliation-export-walkthrough.md`
- Screenshots are in the project evidence archive: `mac-recon/00-first-run-passphrase-remember-off.png`, `mac-recon/01-fictional-client-created.png`, `mac-recon/02-journal-entry-33-33.png`, `mac-recon/03-recon-difference-minus-33-33.png`, `mac-recon/04-recon-cleared-ticked.png`, `mac-recon/05-recon-difference-0-00.png`, `mac-recon/06-recon-history-completed.png`, `mac-recon/07-export-excel-cancelled-app-responsive.png`, `mac-recon/08-export-excel-saved.png`, `mac-recon/09-close-package-pdf-saved.png`, `mac-recon/10-close-package-excel-saved.png`, `mac-recon/11-audit-trail.png`.
- Evidence (internal): `internal/mac-recon-evidence/` — `exports/` (the three saved files + the native-save workbook), `downloads-sha256.txt`, `walk-log.jsonl` (per-step machine log), `native-panel-results.json`, `audit-trail.txt`, `server.log`, and the throwaway drivers `walk.py`, `native_panel_check.py`, `start_server.py`
