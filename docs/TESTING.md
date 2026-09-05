# Testing LedgerTB workflows

Use synthetic books only. Automated tests replace credential storage and never
read or write the real OS vault. Install the development dependencies or use
the verified platform build environment described in `CONTRIBUTING.md`.

## Routine checks

```sh
python -m pytest -q -ra -m "not performance"
```

The normal suite includes the accounting workflow, populated prior-schema
upgrades, actual process-death recovery, and MCP stdio tests. They use existing
dependencies; no live AI provider or external assistant application is needed.

Run the new acceptance coverage together:

```sh
python -m pytest -q -ra \
  tests/test_recurring_snapshots.py \
  tests/test_release_upgrade.py \
  tests/test_cedar_workflow.py \
  tests/test_accounting_crash_recovery.py \
  tests/test_mcp_stdio.py
```

- **Snapshot contract:** edits before approval, future periods, rejection and
  regeneration, legacy primary recovery, legacy reversal review, audit payloads,
  and the recovery explanation/disabled approval button.
- **Prior-schema upgrade:** populated schemas through migrations 023 (v1.6.3)
  and 024 (v1.7.0), repeated migration, backup restore into the current schema,
  and preservation of the original columns and rows. This constructs historical
  schemas from unchanged migrations; it does not install the old binaries.
- **Cedar accounting workflow:** CSV parse/post/reimport, recurring draft and
  reversal approval, reconciliation, independent January balances, MCP report
  values, Excel totals, PDF text, annual supporting review, encrypted reopen,
  stale signoffs, and verified restore of prior history plus a restore event.
- **Process death:** terminate without Python cleanup during generation,
  approval/reversal creation, or import posting. Also terminate after commit
  to simulate a lost response. Check integrity, audit correspondence, and
  retry behavior in the surviving process. Restore's existing failure tests
  remain in `test_backups.py`; these new crash probes do not simulate power loss
  or filesystem hardware failure.
- **MCP transport:** the real server runs in a child process over stdio. Only
  the credential-store boundary is replaced. Discover tools, check emitted
  draft types, deny direct posting at propose level, downgrade permissions,
  reconnect after human resolution, and revoke access without restarting.

## Real browser acceptance

The browser runner creates its own disposable book, fake credential store,
loopback server, and isolated browser session. It starts with Cedar's opening
capital, two posted bank rows, and one pending rent-accrual draft. It uses the
actual unlock gate, navigation, client selector, schedule editor, and approval
controls. It verifies posted records independently after the UI actions.

Install the locked browser tool outside the application's dependency set:

```sh
npm ci --prefix tests/browser-tools --no-audit --no-fund
tests/browser-tools/node_modules/.bin/agent-browser install
python scripts/check_browser_fixture.py \
  --agent-browser "$PWD/tests/browser-tools/node_modules/.bin/agent-browser"
```

On Linux, the browser install supports `--with-deps` for system libraries.
To use an installed Chrome binary, supply `--chrome /path/to/chrome` instead
of downloading a browser. This launches a separate automated session; it does
not attach to an existing browser profile.

Evidence defaults to `output/browser-acceptance/`: command transcript,
server log, a dashboard screenshot, a failure screenshot if applicable, and
`result.json`. The runner exits nonzero on failure and cleans up its server,
browser, and temporary book. Its transcript contains only synthetic values.

The browser scenario checks:

1. A window without the launch token is blocked and receives no token.
2. The authorized window unlocks with the synthetic passphrase.
3. Unsaved entry text and available templates do not follow a client switch.
4. Disabling a schedule's reversal does not change its existing draft.
5. Primary approval creates exactly one reversal draft.
6. Refresh and back navigation retain authorization; dashboard amounts match
   the independent Cedar expectations before reversal.
7. Approving the reversal and refreshing leaves five posted entries, no pending
   drafts, and no entries in the other client.

For interactive exploration, `scripts/browser_fixture.py --data-dir <empty
temporary directory> --port 8617` serves the same scenario. Open
`http://127.0.0.1:8617/?t=cedar-browser-test` and enter
`cedar-browser-passphrase`. This launcher intentionally rejects a nonempty
directory. Stop it when finished; its fake credential store is process-local.

## CI and remaining release acceptance

The Tests workflow runs the Python suite and browser acceptance as separate
jobs on code PRs and manual dispatch. It retains JUnit and browser evidence
for 14 days. Platform-pinned release tests and existing packaging checks remain
in their existing workflows.

The automated browser scenario is a source-app check with synthetic seed data.
It does not certify first-time installation, native file dialogs, real OS-vault
access, download-origin handling, SMB sharing, uninstall/reinstall, or the
distributed Windows/Mac bundles. Keep the real-device checklist in
`WINDOWS-TESTING.md` and the broader release plan in
`RELEASE-REVIEW-1.7.0.md` for those acceptance steps. Generated randomized
workflows and longer performance histories remain later extensions.
