---
cursor:
  subagentId: "bc-aeb65da2-9b3f-58f9-a976-4c760dd9827f"
---

# LedgerTB v1.8.0 — Mac credential vault and installed upgrade walkthrough (release-readiness §4b)

Test method: the upgrade run was two real `open` launches of the installed app with window observation via screencapture and Quartz polling, with page reads done via the installed binary in server mode.

Executed 2026-09-22 07:30–07:46 EDT on Charlie's MacBook Air (macOS 26.6.2 / 25G83), user `charliebarmore`, with Charlie's explicit approval to replace `/Applications/LedgerTB.app` and launch against the real keychain.

**Result: PASS.** The installed 1.7.2 was replaced by the qualified, notarized 1.8.0 bundle. Launched twice from `/Applications` the way a user would, 1.8.0 opened straight into the existing book with **no passphrase prompt and no macOS keychain prompt**, Help & Updates reads **"Installed version: LedgerTB 1.8.0"**, Data Safety still says the passphrase is **remembered on this machine**, and the book's figures are unchanged after the silent 025→027 migration. All nine LedgerTB keychain items are byte-for-byte the same (names, created and modified timestamps) before and after. **1.8.0 is left installed.**

`artifact-manifest.json` was not edited. The field that should now read `true` is **`installed_application_replaced`** (currently `false`).

## 1. What was installed

| Item | Value |
| --- | --- |
| Source bundle | `~/LedgerLabs/ProBooks/output/release-candidate-20260920/dist-final/LedgerTB.app` (qualified build; nothing was rebuilt) |
| Release zip SHA-256 | `7bb1bfe143f057ebdbe80c33970ba3b440917ec7166513743f55148586748ef2` — equals `artifact-manifest.json` `sha256` and `SHA256SUMS` (`shasum -c` OK) |
| dist-final vs zip | Zip re-extracted with `ditto -x -k`; per-file SHA-256 lists of the two bundles are identical (2,306 regular files, 245 symlinks each) |
| Installed main executable SHA-256 | `14520c9a43d3073d33ce6b762aa36792ef3328ddf50b98e9701451ee142dd756` (`/Applications/LedgerTB.app/Contents/MacOS/LedgerTB`; identical in dist-final and in the zip) |
| Installed whole-bundle digest | `93aed0e9c5aa1d6775115573b83bdcf1a77b977f4b49bd1369739fb869a633ee` = SHA-256 of the sorted per-file SHA-256 list of `/Applications/LedgerTB.app`; identical to the same digest of dist-final. List: `internal/mac-upgrade-evidence/installed-1.8.0-file-hashes.txt` |
| Version | `CFBundleShortVersionString` 1.8.0, `CFBundleVersion` 1.8.0 |
| Signature (before and after install) | `codesign --verify --deep --strict` → valid on disk, satisfies Designated Requirement; `Identifier=com.ledgerlabs.ledgertb`; `Authority=Developer ID Application: Ledger Labs LLC (TYU6FU47HR)`; `TeamIdentifier=TYU6FU47HR`; hardened runtime; timestamp Sep 20, 2026 3:19:23 PM; `Notarization Ticket=stapled`; `xcrun stapler validate` OK; `spctl --assess --type execute` → `accepted`, `source=Notarized Developer ID`; no quarantine xattr |
| Same identity as 1.7.2 | 1.7.2 was `Identifier=com.ledgerlabs.ledgertb`, `TeamIdentifier=TYU6FU47HR`, same Developer ID authority (timestamp Sep 7, 2026). Keychain ACLs key on exactly this identity (§4). |

## 2. Backups and before-state (rollback points)

| Item | Value |
| --- | --- |
| 1.7.2 bundle backup | `~/LedgerTB-upgrade-backups/20260922-v1.7.2/LedgerTB.app` (271 MB, `ditto` copy; `codesign --verify --deep --strict` passes on the copy; executable SHA-256 `621e3152400b0f981e44f9809f13ba3bf7ccc721a1a8f0d6580902b5232f1148`, same as the original). Restore = quit LedgerTB, `rm -rf /Applications/LedgerTB.app && ditto <backup> /Applications/LedgerTB.app`. |
| Book backup (pre-migration) | `~/LedgerTB-upgrade-backups/20260922-v1.7.2/books/Cedar-v1.7.2-check.ledgertb.pre-1.8.0` — SHA-256 `5842b2956ce458e161c24a75c183a5a15c329ed8f14ac7013cb81ed9722bf7fb`, the schema-025 file exactly as 1.7.2 left it (1.7.2 tolerates this file; it may not tolerate the migrated one in place). |
| `books.json` backup | `~/LedgerTB-upgrade-backups/20260922-v1.7.2/books/books.json.pre-1.8.0` |
| App data folder | `~/Library/Application Support/ProBooks/` — `accounting.db` (Charlie's own book, 4.5 MB, Aug 30), four `accounting-before-*.db` copies, `backups/` (7 pairs, Jul–Aug), `books.json`. **Not modified**: `accounting.db` SHA-256 `ffc55a0dfadb1b42a0626596da5aad40155fb51270beb147a427b3ccb9dc57c2` before and after; nothing new written under `backups/`. Only `books.json` was rewritten by the app on each open (identical content). |
| Active book | `books.json` `active` = `~/LedgerLabs/ProBooks/output/release-1.7.2/acceptance/fixture/Cedar-v1.7.2-check.ledgertb` — the **fictional** "Cedar Demo Services" fixture from the 1.7.2 acceptance (`expected.json`: 4 posted entries, TB 13,700/13,700, assets 12,200, net income 1,000). This is the book both versions opened on launch. `accounting.db` was deliberately **not** opened in 1.8.0 so it stays un-migrated (schema 025) and untouched. |
| Stale processes | pids 96986 (`output/usability-overnight-20260918/dist/LedgerTB.app`), 22125 and 74804 (`.macos-venv/bin/python run_ledgertb.py`), all from Sep 19, verified by command line and sent SIGTERM; gone within 3 s. No other LedgerTB launcher processes existed. (The many `mcp_server.py` processes under `.worktrees/tpl-m1-dogfood/` belong to a different project and were left alone.) |

## 3. Keychain items — before and after (names only; no secrets read or printed)

`security dump-keychain` filtered to services `com.ledgerlabs.ledgertb` / `com.ledgerlabs.probooks`. **Identical before and after** (`diff` of the two listings is empty), including created/modified timestamps — nothing was created, re-keyed, or re-ACL'd.

| Service | Account | Created | Note |
| --- | --- | --- | --- |
| com.ledgerlabs.ledgertb | `book_key_df70611c572d36e9` | 2026-09-07 | Remembered key for the Cedar fixture (`sha256(path)[:16]`). **This is the item the upgrade exercised.** |
| com.ledgerlabs.ledgertb | `book_key_a25bc7e5b168b5d3` | 2026-08-10 | Remembered key for `accounting.db` (not opened) |
| com.ledgerlabs.ledgertb | `anthropic_api_key` | 2026-08-10 | Read by both versions on Firm Settings (§5 step 13) |
| com.ledgerlabs.ledgertb | `mcp_book:a25bc7e5…:book_id` / `:access_level` / `:db_key` / `:export_roots` | 2026-08-10/11 | Assistant credentials for `accounting.db` |
| com.ledgerlabs.probooks | `anthropic_api_key`, `book_key_a25bc7e5b168b5d3` | 2026-08-01/04 | Legacy-service copies |

Full listings: `internal/mac-upgrade-evidence/keychain-before.txt`, `keychain-after.txt`.

## 4. Why no prompt was expected, and why the result is meaningful

`security dump-keychain -a` (ACL only) shows every LedgerTB item's decrypt ACL carries the **designated requirement** `identifier "com.ledgerlabs.ledgertb" and anchor apple generic and … certificate leaf[subject.OU] = TYU6FU47HR`, with partition `teamid:TYU6FU47HR` — not a cdhash. The 1.8.0 bundle satisfies that requirement (same bundle id, same Developer ID team), so macOS grants access silently. Two details make this a real test rather than a foregone conclusion:

- The exercised item `book_key_df70611c572d36e9` lists as its only trusted application a path that **no longer exists** (`output/release-1.7.2/acceptance/mac/LedgerTB.app`, status `-2147415734`). Access from `/Applications/LedgerTB.app` therefore depends entirely on requirement matching, which is exactly what a changed signature identity would break.
- The ACL check is made against the **server child process** (`/Applications/LedgerTB.app/Contents/MacOS/LedgerTB` in `LEDGERTB_MODE=server`), which is the process that calls `keyring`. A native `open` launch spawns that child from the installed bundle, so the genuine path was exercised.

## 5. Steps and results (numbered as in release-readiness §4b)

Method note: no Accessibility or Automation grant is available to this session, so nothing in the native window was clicked. Native launches were done with `open /Applications/LedgerTB.app`; windows and dialogs were observed with Quartz `CGWindowListCopyWindowInfo` polled every 0.5 s (owners such as `SecurityAgent`, `CoreServicesUIAgent`, `UserNotificationCenter` would be a keychain / Gatekeeper / permission dialog), and captured with `screencapture -l <windowid>` (Screen Recording was available). Pages that need navigation (Help & Updates, Data Safety, Firm Settings, Audit Trail) were read by running the **installed binary itself** in `LEDGERTB_MODE=server` against the real data folder and real keychain (no fake API-key env, no data-dir override) and loading the token URL in headless Chromium — the same binary, code signature and vault path, after the native window had been quit. Quit = SIGTERM to the launcher process; the server child's parent-watch releases the book lock and exits (verified: lock file removed within 1 s each time).

| Step | Expected | Result | Evidence |
| --- | --- | --- | --- |
| 1 | 1.7.2 shows "Installed version: LedgerTB 1.7.2" | PASS — native launch of the installed 1.7.2 opened straight to Dashboard · Cedar Demo Services; Help & Updates (server-mode read of the same 1.7.2 binary) "Installed version: LedgerTB 1.7.2" | Screenshots are in the project evidence archive: `mac-upgrade/00-baseline-1.7.2-native-dashboard-no-prompt.png`, `mac-upgrade/01-baseline-1.7.2-help-and-updates.png`. |
| 2 | Passphrase remembered, Forget button present | PASS — Data Safety: "This book's passphrase is remembered on this machine (system credential vault) — the app opens it without asking." + **Forget passphrase** | Screenshot is in the project evidence archive: `mac-upgrade/02-baseline-1.7.2-data-safety-remembered.png`. |
| 3 | Create verified backup | **Substituted** (needs a native click): file-level copy of the pre-migration book to the backup folder (§2). Data Safety on both versions reports "No verified backup exists for this book" for this fixture — a pre-existing condition, not a regression. | §2 |
| 4 | Note counts | Total Assets $12,200.00 · Total Liabilities $1,200.00 · Fiscal YTD Net Income $1,000.00 · Pending Imports 0 · "Journal Entries (1 draft)" · Audit Trail 40 entries · "In balance — assets 12,200.00 = liabilities 1,200.00 + equity 10,000.00 + net income 1,000.00" | page text in `internal/mac-upgrade-evidence/page-text/baseline-172-*.txt` |
| 5 | Quit fully, `pgrep` empty | PASS — launcher 12114 + server 12191 exited in 1 s; lock file removed; no LedgerTB process or window | launch log |
| 6 | Verify archive: sha, codesign, stapler, spctl | PASS — §1 | `internal/mac-upgrade-evidence/installed-1.8.0-codesign.txt` |
| 7 | Replace bundle (`rm -rf` then `ditto`) | PASS — 07:40:46–07:40:48, after re-verifying the backup and confirming no LedgerTB process | — |
| 8 | Installed copy passes codesign; Authority = Ledger Labs Developer ID | PASS — §1 (valid, stapled, `spctl` accepted, per-file hashes equal dist-final) | — |
| 9 | **Launch: no Gatekeeper dialog, straight into books, no passphrase prompt, no keychain prompt** | **PASS** — `open /Applications/LedgerTB.app` at 07:41:04; launcher 22784, server 22982 healthy at t=5.5 s; loading skeleton at t=6 s, Dashboard · Cedar Demo Services fully rendered by t=12 s and unchanged at t=40 s. Zero windows from `SecurityAgent`, `CoreServicesUIAgent`, `UserNotificationCenter` or any other dialog owner during 50 s of polling; the only LedgerTB window was the main window. | Screenshots are in the project evidence archive: `mac-upgrade/03-1.8.0-first-launch-t06-loading.png`, `mac-upgrade/04-1.8.0-first-launch-dashboard-no-prompt.png`, `mac-upgrade/05-1.8.0-first-launch-full-screen-no-dialogs.png`, `mac-upgrade/06-1.8.0-first-launch-t40-stable.png`. Launch log: `internal/mac-upgrade-evidence/launch-logs/first-180-launch-log.json` |
| 10 | Help & Updates shows 1.8.0 | **PASS** — "Installed version: LedgerTB 1.8.0" | Screenshot is in the project evidence archive: `mac-upgrade/07-1.8.0-help-and-updates-version.png`. |
| 11 | Data Safety still remembered; no "Unencrypted migration copy found"; backup status intact | **PASS** — same "remembered on this machine" caption + Forget passphrase; "Unencrypted migration copy removed · Pass"; backup section identical to 1.7.2 (same six notices, same "5 older backup(s)" line) | Screenshot is in the project evidence archive: `mac-upgrade/08-1.8.0-data-safety-remembered.png`. |
| 12 | Migrations silent; counts match; Import shows new panel; Audit Trail clean | **PASS (migration/counts/audit)** — book file rewritten in place 07:41:09 (421,888 → 438,272 B), no startup error, no alert or traceback on any page; Dashboard $12,200.00 / $1,200.00 / $1,000.00 / 0 pending and "Journal Entries (1 draft)" unchanged; Audit Trail still 40 entries, no error rows. **Not observed:** the provider panel and "Save review for later" render only inside an active import review; nothing was uploaded into the fixture. (These were exercised on the qualified build in the §4a walkthrough and the release review.) | Screenshots are in the project evidence archive: `mac-upgrade/09-1.8.0-dashboard-browser-view.png`, `mac-upgrade/11-1.8.0-audit-trail.png`. Page text: `page-text/after-180-*.txt` |
| 13 | Firm Settings vault-stored values survive | **PASS** — 1.8.0 Firm Settings: "An Anthropic key is saved. Available immediately for import review." (1.7.2 wording: "AI categorization is enabled for this session."). Both are the app reading `anthropic_api_key` from the vault, so the API-key item is also readable under the new signature. Assistant access for this fixture is off on both versions (shared-drive path → read-only), unchanged. | Screenshot is in the project evidence archive: `mac-upgrade/10-1.8.0-firm-settings-anthropic-key-saved.png`. |
| 14 | Quit and relaunch: still no prompt, still 1.8.0 | **PASS** — second `open` at 07:44:00: launcher 29387 / server 29433, healthy at t=2.8 s, Dashboard rendered, no dialog windows in 28 s of polling; quit cleanly in 1 s, lock released. Bundle unchanged (same executable, `codesign` still valid at the end). | Screenshot is in the project evidence archive: `mac-upgrade/12-1.8.0-second-launch-dashboard-no-prompt.png`. Launch log: `launch-logs/second-180-launch-log.json` |
| 15 | Rollback rehearsal (optional) | Not performed (optional; would have required re-installing 1.7.2 and restoring the book). The rollback assets exist and are verified (§2). | — |

Final state: `/Applications/LedgerTB.app` = 1.8.0 (codesign valid); no LedgerTB processes running; `~/LedgerLabs/ProBooks` untouched (`codex/jev-overnight` @ `37bcf76`, same dirty set: ` M CLAUDE.md`, untracked `.worktrees/ output/ tmp/`); `artifact-manifest.json` untouched (mtime Sep 20 15:44).

## 6. Observations (none blocking)

1. **Unrelated macOS notification in the full-screen frame.** Screenshot is in the project evidence archive: `mac-upgrade/05-1.8.0-first-launch-full-screen-no-dialogs.png`. It shows a top-right banner "App Background Activity — 'install.sh' can run in the background". The window monitor recorded that Notification Center window at t=0.0 of the first 1.8.0 launch, i.e. it was already on screen before the LedgerTB launcher process appeared; the LedgerTB bundle contains no `install.sh`; it is a passive notification (no button needed) and not a keychain, Gatekeeper or permission prompt. Time-coincident, not caused by LedgerTB.
2. **Server-mode SIGTERM leaves a stale book lock.** When the installed binary is run directly in `LEDGERTB_MODE=server` (no launcher parent) and stopped with SIGTERM to its process group, the `.lock` beside the book stays behind (holder pid dead). The normal native path releases it correctly (parent-watch), and the next opener takes a stale same-machine lock over silently, so this only affects the diagnostic mode. Cosmetic.
3. **Migration wrote no side-by-side backup.** The 025→027 upgrade rewrote the book in place with nothing new under `backups/` or beside the book. Consistent with the release notes' "back up before upgrading" advice; the file-level copy in §2 is the rollback.
4. **Book-key ACL still names a vanished path.** `book_key_df70611c572d36e9`'s only trusted-app entry points at the deleted Sep 7 acceptance bundle; access from `/Applications` works purely via requirement matching (§4). Harmless as long as the Developer ID identity stays stable — which is precisely the CLAUDE.md rule.
5. **Fixture Data Safety shows "action needed" on both versions** (no verified backup, shared-drive path notices). Pre-existing property of this fixture, not a 1.8.0 change.

## 7. Files produced

- This report: `docs/mac-credential-upgrade-walkthrough.md`
- Screenshots are in the project evidence archive: `mac-upgrade/00-baseline-1.7.2-native-dashboard-no-prompt.png`, `mac-upgrade/01-baseline-1.7.2-help-and-updates.png`, `mac-upgrade/02-baseline-1.7.2-data-safety-remembered.png`, `mac-upgrade/03-1.8.0-first-launch-t06-loading.png`, `mac-upgrade/04-1.8.0-first-launch-dashboard-no-prompt.png`, `mac-upgrade/05-1.8.0-first-launch-full-screen-no-dialogs.png`, `mac-upgrade/06-1.8.0-first-launch-t40-stable.png`, `mac-upgrade/07-1.8.0-help-and-updates-version.png`, `mac-upgrade/08-1.8.0-data-safety-remembered.png`, `mac-upgrade/09-1.8.0-dashboard-browser-view.png`, `mac-upgrade/10-1.8.0-firm-settings-anthropic-key-saved.png`, `mac-upgrade/11-1.8.0-audit-trail.png`, `mac-upgrade/12-1.8.0-second-launch-dashboard-no-prompt.png`. 00, 03, 04, 06, and 12 are screencapture shots of the real native window, 05 is the full screen, and the rest are browser renders of the installed binary in server mode.
- Evidence (internal): `internal/mac-upgrade-evidence/` — `keychain-before.txt`, `keychain-after.txt`, `installed-1.7.2-codesign.txt`, `installed-1.8.0-codesign.txt`, `dist-final-file-hashes.txt`, `zip-extract-file-hashes.txt`, `installed-1.8.0-file-hashes.txt`, `books-sha-before.txt`, `books.json.before`, `data-dir-before.txt`; `launch-logs/` (window/process/port polling logs for the three native launches, server-mode page-read results and server logs); `page-text/` (full page text for every page read on 1.7.2 and 1.8.0); `scripts/` (`windows.py`, `launch_monitor.py`, `server_pages.py`)
- Machine-side artifacts left in place: `~/LedgerTB-upgrade-backups/20260922-v1.7.2/` (rollback point, keep until 1.8.0 is published); `/tmp/ledgertb-upgrade-20260922/` (scratch copies of the above, deletable)
