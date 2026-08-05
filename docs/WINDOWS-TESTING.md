# ProBooks on Windows — smoke test

The Windows bundle is built by the `Windows build spike` GitHub Actions
workflow and attached to the run as the `ProBooks-windows` artifact. This
checklist is the part CI cannot do: a human confirming the app runs and
works on real Windows.

## Getting the build onto the PC

1. On the Windows PC, sign in to GitHub in a browser → the ProBooks repo →
   **Actions** → latest **Windows build spike** run → **Artifacts** →
   download `ProBooks-windows`.
2. **Extract with File Explorer's "Extract All…"** (not a scripting tool) —
   that propagates mark-of-the-web so SmartScreen behaves the way it will
   for a real member. Extract the whole zip; don't run the exe from inside
   it.

## Expected friction (not bugs)

- **SmartScreen** may say "Windows protected your PC" — click
  **More info → Run anyway**. The build is unsigned for now. *Note what
  the dialog actually says — we've never observed it in the wild.*
- If the app window never appears, install the **Microsoft Edge WebView2
  runtime** (preinstalled on most Win 10/11; free from Microsoft).

## Actual bugs if you see them

- **A console window.** This build is windowed; any black console box is
  a regression.
- Default red Streamlit theme, a "Deploy" button top-right, or a sidebar
  nav that starts with lowercase "app" — all mean the bundled config went
  missing.
- Generic exe icon (should be the navy PB mark).

## The test

Data lives under your Windows user profile — a fresh, empty ProBooks,
nowhere near real books.

1. **Launch** `ProBooks.exe` → app window opens, navy theme, no console.
2. **Passphrase setup** appears → set a throwaway passphrase and tick
   **"Remember on this computer"** (exercises Windows Credential
   Manager — a backend CI cannot verify).
3. **Create a client** → default chart of accounts seeds.
4. **Import** — save this as `test.csv`, import into the cash account,
   Bank Account convention:

   ```csv
   Date,Description,Amount
   2026-07-03,ACME COFFEE,-12.50
   2026-07-07,CLIENT PAYMENT,1500.00
   2026-07-11,OFFICE DEPOT,-84.20
   2026-07-18,DOMAIN RENEWAL,-19.99
   2026-07-25,CLIENT PAYMENT,750.00
   ```

5. **Categorize** — on one row use **➕ Add new account…** in the dropdown
   and confirm the created account is selected in place. Post.
6. **Reports** → Trial Balance balanced; General Ledger shows all
   accounts; on the TB Worksheet, change the **Period** dropdown and
   confirm the numbers follow.
7. **Journal Entries → Drafts** view exists (empty is fine).
8. **Close package** — TB Worksheet → export the PDF and the Excel; both
   open. (reportlab/openpyxl and the date formatting on Windows.)
9. **Backup** — Data Safety → Create verified backup → passes.
10. **Restart proof** — close the app fully, relaunch: it should open
    **straight into the books with no passphrase prompt** (the remembered
    key). Data Safety shows "remembered on this machine" with a Forget
    button.
11. **(Optional) Firm mode** — lock screen (after Forget) → Book file →
    create a second book at some path; confirm the in-use lock file
    appears next to it while open.

## Report back

Whatever broke, plus: Windows version, what SmartScreen showed, and
whether the window looked right (theme, fonts, icon, sizing). Console
output is gone in this build — if something dies silently, check
`%LOCALAPPDATA%\ProBooks\server.log` (or the equivalent under your user
data dir) and copy it whole.
