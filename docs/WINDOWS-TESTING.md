# ProBooks on Windows — smoke test

The Windows bundle is built by the `Windows build spike` GitHub Actions
workflow and attached to the run as the `ProBooks-windows` artifact. This
checklist is the part CI cannot do: a human confirming the app runs and
works on real Windows.

## Getting the build onto the PC

1. On the Windows PC, sign in to GitHub in a browser → the ProBooks repo →
   **Actions** → latest **Windows build spike** run → **Artifacts** →
   download `ProBooks-windows-installer`.
2. **Download through the browser**, not a scripting tool — that applies
   mark-of-the-web, so SmartScreen behaves the way it will for a real
   member. A file fetched with `gh run download` carries no such mark and
   tells you nothing about what a member sees.
3. Run the installer. It installs per-user under
   `%LOCALAPPDATA%\Programs\ProBooks`, needs no administrator password, and
   adds a Start Menu entry.

> The `ProBooks-windows` artifact is the raw folder, not the installer. It is
> useful for inspecting a bundle, but **extracting it with Explorer marks every
> file as internet-downloaded and the app will refuse to start** — see the
> mark-of-the-web section below. Test the installer; that is what members get.

## Expected friction (not bugs)

- **SmartScreen** says "Windows protected your PC" — click **More info**, then
  the button that runs it anyway. The build is unsigned for now.

  **Both wordings observed on the same machine** (Windows 11 Pro 26200):

  | Launched | 2026-08-05 (bare `ProBooks.exe` from a zip) | 2026-08-07 (the installer) |
  |---|---|---|
  | Second button | **"open anyway"** | **"Run anyway"** |

  Same OS, same unsigned build, different label. **Tell people what the button
  does, never what it says** — a member hunting for "Run anyway" and seeing
  "open anyway" concludes they downloaded the wrong thing.

  The installer dialog also names the file and shows **Publisher: Unknown
  publisher**. That line is the code-signing gap in plain sight: a Developer ID
  / OV certificate would put *Ledger Labs LLC* there instead, and reputation
  would eventually retire the dialog altogether. Until then, expect this once
  per new build.
- If the app window never appears, install the **Microsoft Edge WebView2
  runtime** (preinstalled on most Win 10/11; free from Microsoft).

## Mark-of-the-web — the reason we ship an installer

Windows tags every file extracted from a downloaded zip as internet-sourced
(`Zone.Identifier`, `ZoneId=3`). The .NET Framework then refuses to load the
bundled `Python.Runtime.dll`, pywebview's Windows backend cannot start, and the
app dies before its window appears. Found on a clean Windows 11 machine on
2026-08-05, *after* SmartScreen had already been clicked through — 2,848 of
2,848 extracted files were tagged.

The installer writes its own files, so nothing is tagged and the app just runs
(verified: 0 of 2,808 installed files carry the tag). If you are testing a zip
anyway, ProBooks now detects this and says what to do instead of showing a
traceback. To clear it by hand:

```powershell
Get-ChildItem "<extracted folder>" -Recurse -File | Unblock-File
```

Or right-click the **zip** → Properties → tick **Unblock** → OK, *then* extract.

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

0. **Install** — run the installer, click through SmartScreen. No
   administrator password should ever be requested. Confirm a **ProBooks**
   entry appears in the Start Menu and in Settings → Apps.
1. **Launch** from the Start Menu → app window opens, navy theme, no console.
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
12. **Uninstall proof** — Settings → Apps → ProBooks → Uninstall. It should
    remove cleanly *and leave your books alone*: the database lives in
    `%LOCALAPPDATA%\LedgerLabs\ProBooks`, outside the install folder, so it
    must still be there afterwards. Reinstalling should open the same books.
    An uninstall that deletes a client's books is the worst bug this app
    could have.

## Report back

Whatever broke, plus: Windows version, what SmartScreen showed, and
whether the window looked right (theme, fonts, icon, sizing). Console
output is gone in this build — if something dies silently, check
`%LOCALAPPDATA%\ProBooks\server.log` (or the equivalent under your user
data dir) and copy it whole.
