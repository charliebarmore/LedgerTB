# ProBooks on Windows — smoke test

The Windows bundle is built by the `Windows build spike` GitHub Actions
workflow and attached to the run as the `ProBooks-windows` artifact. This
checklist is the part CI cannot do: a human confirming the app actually
runs and works on real Windows.

## Getting the build onto the PC

1. On the Windows PC, sign in to GitHub in a browser → the ProBooks repo →
   **Actions** → latest **Windows build spike** run → **Artifacts** →
   download `ProBooks-windows`.
2. Right-click the downloaded zip → **Properties** → if there is an
   **Unblock** checkbox, tick it → OK. (Removes mark-of-the-web.)
3. **Extract the whole zip** to a folder (e.g. Desktop). Do not run the
   exe from inside the zip — the app needs its support files beside it.

## Expected friction (not bugs)

- **SmartScreen** may say "Windows protected your PC" — click
  **More info → Run anyway**. The build is unsigned for now.
- A **console window** opens alongside the app. Deliberate in this build —
  it shows errors during testing; the shipping build will hide it.
- If the app window never appears, install the **Microsoft Edge WebView2
  runtime** (preinstalled on most Win 10/11; free from Microsoft).

## The test

Data lives under your Windows user profile — this is a fresh, empty
ProBooks, not your real books.

1. **Launch** `ProBooks.exe` → app window opens.
2. **Passphrase setup** appears (new database) → set a throwaway
   passphrase → you land on the app.
3. **Create a client** → confirm the default chart of accounts seeded.
4. **Import** — save the CSV below as `test.csv`, import into the cash
   account, Bank Account convention:

   ```csv
   Date,Description,Amount
   2026-07-03,ACME COFFEE,-12.50
   2026-07-07,CLIENT PAYMENT,1500.00
   2026-07-11,OFFICE DEPOT,-84.20
   2026-07-18,DOMAIN RENEWAL,-19.99
   2026-07-25,CLIENT PAYMENT,750.00
   ```

5. **Categorize** — pick categories on a few rows; on one row use
   **➕ Add new account…** in the dropdown and confirm the created
   account is selected in place. Post the transactions.
6. **Reports** → Trial Balance balanced; General Ledger shows all
   accounts.
7. **Close package** — Trial Balance Worksheet → export the PDF and the
   Excel; both should open. (Exercises reportlab/openpyxl on Windows.)
8. **Backup** — Data Safety → Create verified backup → passes.
9. **Restart proof** — close the app fully (console window too), relaunch,
   unlock with the passphrase, confirm the client and entries are there.
10. **(Optional) API key** — Firm Settings → save any string as the key →
    confirm it reports saved (exercises Windows Credential Manager), then
    remove it.

## Report back

Whatever broke, plus: Windows version, and whether the window looked
right (fonts, sizing, scrolling). Console tracebacks are gold — copy the
whole thing.
