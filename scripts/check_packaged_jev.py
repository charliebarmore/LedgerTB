"""Exercise an isolated frozen Mac app with fictional books and a fake vault.

The application must be a disposable build under this repository's output/.
Only a test-only keyring backend is added to that bundle. Production source must
match the checkout. --key-file explicitly enables one fictional live Jev request;
the credential stays in its original file and never enters browser commands.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--app", type=Path)
    target.add_argument("--source", action="store_true", help="Exercise this checkout with the same fake-vault full-app workflow")
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--fixture-template", type=Path,
                        help="Copy a previously prepared synthetic fixture under output/ into a new disposable directory")
    parser.add_argument("--startup-timeout", type=float, default=180,
                        help="Diagnostic startup allowance; increasing it does not establish startup performance")
    parser.add_argument("--output", type=Path, default=ROOT / "output/packaged-jev-check")
    parser.add_argument("--agent-browser", default=str(ROOT / "tests/browser-tools/node_modules/.bin/agent-browser"))
    args = parser.parse_args()
    app = args.app.resolve() if args.app else None
    if app and (not app.is_relative_to(ROOT / "output") or app.suffix != ".app"):
        parser.error("Use a disposable .app under this checkout's output directory")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = app / "Contents/Frameworks" if app else ROOT
    if app and not bundle.resolve().is_relative_to(app):
        parser.error("The disposable bundle must not link its Frameworks directory outside the app")
    sources = ["config.py", ".streamlit/config.toml", "database/connection.py", "pages/4_Import_Transactions.py", "services/jev_categorization.py",
               "utils/jev_review.py", "utils/import_review.py", "services/csv_import.py",
               "pages/2_Journal_Entries.py", "pages/7_Dashboard.py", "pages/12_Firm_Settings.py",
               "services/review_categorization.py", "utils/ai_review.py", "utils/ui.py",
               "services/import_review_drafts.py", "utils/review_recovery.py", "utils/recovery.py",
               "services/worksheet_export.py", "services/worksheet_export_cache.py",
               "pages/1_Trial_Balance_Worksheet.py", "pages/3_Chart_of_Accounts.py", "database/migrations/026_import_review_drafts.sql",
               "utils/review_guard.py", "utils/client_selector.py", "pages/9_Data_Safety.py",
               "utils/desktop_review_status.py", "utils/review_lifecycle.py", "services/book_generation.py",
               "services/review_recovery_store.py", "services/posting.py", "services/backups.py",
               "database/migrations/027_review_recovery.sql"]
    hashes = {}
    for name in sources:
        actual = (bundle / name).read_bytes()
        assert actual == (ROOT / name).read_bytes(), f"Rebuild: packaged {name} differs from checkout"
        hashes[name] = hashlib.sha256(actual).hexdigest()
    if app:
        backend_source = ROOT / "tests/helpers/packaged_fake_vault.py"
        backend_target = bundle / "packaged_fake_vault.py"
        if not backend_target.exists() or backend_target.read_bytes() != backend_source.read_bytes():
            shutil.copyfile(backend_source, backend_target)
        # Adding the fixture changes the resource seal. Repair this disposable
        # bundle with an ad hoc signature, never a developer certificate/keychain.
        with (output / "signature.log").open("w") as signing_log:
            verification = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                                          stdout=signing_log, stderr=signing_log, timeout=600)
            if verification.returncode:
                subprocess.run(["codesign", "--force", "--deep", "--options", "runtime", "--sign", "-",
                                "--entitlements", str(ROOT / "scripts/entitlements.plist"), str(app)],
                               stdout=signing_log, stderr=signing_log, check=True, timeout=600)
                subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                               stdout=signing_log, stderr=signing_log, check=True, timeout=600)
    session = "jev-packaged-" + uuid.uuid4().hex[:10]
    namespace = "jv-" + session.rsplit("-", 1)[1][:8]  # Stay below macOS's socket-path limit.
    cli = [args.agent_browser, "--namespace", namespace, "--session", session, "--pin-tab", "--json"]
    transcript, checks = [], []
    page_opened = False

    def command(*parts):
        # Repeat only read-only waits on a contended machine. Never replay a
        # click, fill, upload, posting action or cloud request after a timeout.
        for attempt in range(6 if parts[0] == "wait" else 1):
            started = time.monotonic()
            try:
                result = subprocess.run(cli + list(parts), capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                transcript.append({"command": list(parts), "subprocess_timeout": True,
                                   "elapsed_seconds": time.monotonic() - started})
                (output / "browser.json").write_text(json.dumps(transcript, indent=2))
                if parts[0] == "wait" and attempt < 5:
                    continue
                raise
            transcript.append({"command": list(parts), "stdout": result.stdout, "stderr": result.stderr})
            (output / "browser.json").write_text(json.dumps(transcript, indent=2))
            response = json.loads(result.stdout)
            if response.get("success"):
                return response["data"]
            if parts[0] != "wait" or "timed out" not in str(response.get("error", "")).lower():
                break
        raise AssertionError((parts, result.stdout, result.stderr))

    def snap():
        command("wait", '[data-testid="stApp"][data-test-script-state="notRunning"]')
        return command("snapshot", "-i")["snapshot"]

    def show_panel(wanted=None):
        state = snap()
        for label in ("Select rows for actions", "AI suggestions", "Change category", "Sort"):
            opened = f'button "Close {label}"' in state
            if opened != (label == wanted):
                command("find", "role", "button", "click", "--name", f"Close {label}" if opened else label, "--exact")
                state = snap()

    def show_opinions(count=1, failed=False):
        command("wait", "--fn", "Array.from(document.querySelectorAll('summary')).some(s => "
                f"s.textContent.includes('AI opinions ({count})') && s.textContent.includes('Request failed') === {str(failed).lower()})")
        # Open the visible per-row disclosure; no application state is injected.
        command("eval", "Array.from(document.querySelectorAll('details')).filter(d => "
                "d.querySelector('summary')?.textContent.includes('AI opinions')).forEach(d => {"
                "if (!d.open) d.querySelector('summary').click(); })")
        command("eval", "Array.from(document.querySelectorAll('summary')).find(s => "
                "s.textContent.includes('AI opinions')).scrollIntoView({block:'start',behavior:'instant'})")


    def click(role, name, exact=True):
        if name in ("Selected rows", "Select all", "Clear selection"):
            show_panel("Select rows for actions")
        elif name in ("Ask Jev for suggestions", "Retry failed Jev requests", "Choose another AI", "Ask Anthropic for suggestions", "Ask OpenAI for suggestions"):
            show_panel("AI suggestions")
        elif role != "option":
            show_panel()
        snap()
        if role == "button":
            command("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => "
                    f"(b.textContent.trim() === {json.dumps(name)} || b.getAttribute('aria-label') === {json.dumps(name)}) && !b.disabled)")
            # Rerenders can move a button above the visible viewport. Center it
            # before the ordinary pointer click; never force through an overlay.
            command("eval", "Array.from(document.querySelectorAll('button')).find(b => "
                    f"b.textContent.trim() === {json.dumps(name)} || b.getAttribute('aria-label') === {json.dumps(name)})"
                    ".scrollIntoView({block: 'center', behavior: 'instant'})")
            # Scrolling can leave the pointer over Retry and open its help
            # tooltip across Ask. Move to the observed empty sidebar margin.
            command("mouse", "move", "10", "10")
            command("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => {"
                    f"if (b.textContent.trim() !== {json.dumps(name)} && b.getAttribute('aria-label') !== {json.dumps(name)}) return false;"
                    "const r = b.getBoundingClientRect(); return !b.disabled && "
                    "b.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); })")
        command("find", "role", role, "click", "--name", name, *(["--exact"] if exact else []))

    def wait(text):
        command("wait", "--text", text)

    def fill(name, value):
        snap()
        command("find", "role", "textbox", "fill", value, "--name", name, "--exact")

    def check(name):
        show_panel("AI suggestions" if name == "Send the selected transaction information to TypeSafe" else None)
        snap()
        command("find", "role", "checkbox", "check", "--name", name, "--exact")

    def uncheck(name):
        line = next(line for line in snap().splitlines() if f'checkbox "{name}"' in line)
        command("uncheck", "@" + re.search(r"ref=(e\d+)", line)[1])

    with tempfile.TemporaryDirectory(prefix="ledgertb-packaged-jev-") as scratch_name:
        scratch = Path(scratch_name)
        if args.fixture_template:
            template = args.fixture_template.resolve()
            assert template.is_relative_to(ROOT / "output"), "Fixture template must be under output/"
            fixture = json.loads((template / "synthetic-fixture.json").read_text())
            assert fixture.get("synthetic") is True and len(fixture["books"]) == 2
            for book in fixture["books"]:
                source = Path(book["path"]).resolve()
                assert source.is_relative_to(template), "Fixture book must stay inside its synthetic template"
                destination = scratch / source.relative_to(template)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                book["path"] = str(destination)
            (scratch / "synthetic-fixture.json").write_text(json.dumps(fixture))
            (scratch / "books.json").write_text(json.dumps({
                "active": str(scratch / "accounting.db"),
                "recent": [book["path"] for book in fixture["books"]],
            }))
            shutil.copyfile(template / "synthetic-import.csv", scratch / "synthetic-import.csv")
        else:
            subprocess.run([sys.executable, str(ROOT / "scripts/packaged_jev_fixture.py"),
                            "--data-dir", str(scratch)], check=True, timeout=300)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        for prefix in ("LEDGERTB", "PROBOOKS"):
            for suffix in ("DB_PATH", "BACKUP_DIR", "PARENT_PID"):
                env.pop(f"{prefix}_{suffix}", None)
        env.update(LEDGERTB_DATA_DIR=str(scratch), LEDGERTB_FIXTURE_DIR=str(scratch),
                   PYTHON_KEYRING_BACKEND="packaged_fake_vault.Keyring", PYTHON_DOTENV_DISABLED="1",
                   ANTHROPIC_API_KEY="fake-not-used", LEDGERTB_MODE="server", LEDGERTB_PORT=str(port),
                   LEDGERTB_UI_TOKEN=session, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
        if args.source:
            env["PYTHONPATH"] = str(ROOT / "tests/helpers")
        env["OPENAI_API_KEY"] = "fake-not-used"
        env["TYPESAFE_API_KEY"] = "fake-not-used"
        env.pop("LEDGERTB_FIXTURE_KEY_FILE", None)
        if args.key_file:
            env["LEDGERTB_FIXTURE_KEY_FILE"] = str(args.key_file.expanduser().resolve())
        with (output / "server.log").open("w") as log:
            launch = [str(app / "Contents/MacOS/LedgerTB")] if app else [sys.executable, str(ROOT / "run_ledgertb.py")]
            server = subprocess.Popen(launch, env=env,
                                      cwd=scratch, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + args.startup_timeout
                while True:
                    assert server.poll() is None, "Packaged server exited; inspect server.log"
                    try:
                        with urlopen(base + "/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    assert time.monotonic() < deadline, "Packaged server did not start"
                    time.sleep(.2)
                command("set", "viewport", str(args.width), str(args.height))
                command("open", base + "/?t=" + session)
                page_opened = True
                wait("Enter your passphrase")
                assert (scratch / "fake-vault-loaded").exists()
                fill("Passphrase", "fictional-packaged-acceptance-only")
                click("button", "Unlock")
                wait("Viewing: Cedar Synthetic Studio")
                click("link", "Import Transactions", exact=False)
                wait("Upload CSV file")
                command("upload", 'input[type="file"]', str(scratch / "synthetic-import.csv"))
                wait("Check totals")
                snap()
                command("eval", "Array.from(document.querySelectorAll('h3')).find(h => h.textContent.includes('Check totals')).scrollIntoView({block:'center'})")
                wait("$81.58")
                assert "$81.58" in command("get", "text", "body")["text"]
                command("screenshot", str(output / "upload-totals.png"))
                check("The account, columns and totals are correct")
                click("button", "Continue to review")
                wait("Review & Categorize Transactions")
                click("button", "Exclude All")
                click("combobox", "Selected rows")
                click("option", "2026-09-01 | Cedar Paper receipt printer paper solely for design studio | $-33.33")
                command("press", "Tab")
                check("Send the selected transaction information to TypeSafe")
                (scratch / "offline").touch()
                click("button", "Ask Jev for suggestions")
                snap()
                show_opinions(failed=True)
                wait("TypeSafe could not be reached")
                show_panel()
                assert snap().count('checkbox "Include for posting"') == 2
                click("button", "Ask Jev for suggestions")
                snap()
                failed_requests = [json.loads(line) for line in (scratch / "requests.jsonl").read_text().splitlines()]
                assert len(failed_requests) == 1 and not failed_requests[0]["network_attempted"]
                (scratch / "offline").unlink()
                click("button", "Retry failed Jev requests")
                snap()
                show_opinions()
                wait("Suggested account:")
                click("button", "Accept account suggestion")
                wait("Account accepted.")
                show_panel()
                assert snap().count('checkbox "Include for posting"') == 2
                assert len([line for line in snap().splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line]) == 2
                click("button", "Ask Jev for suggestions")
                snap()
                requests = [json.loads(line) for line in (scratch / "requests.jsonl").read_text().splitlines()]
                assert len(requests) == 2 and requests[1]["success"]
                assert sum(row["network_attempted"] for row in requests) == int(bool(args.key_file))
                checks += ["packaged SQLCipher unlock and CSV import", "explicit Jev request and human acceptance", "acceptance preserves exclusion", "rerun and repeated request reuse"]
                checks += ["offline failure preserves staged rows", "failed result reuse and explicit recovery retry"]
                for other_provider in ("Anthropic", "OpenAI"):
                    click("button", "Choose another AI")
                    snap()
                    command("find", "role", "checkbox", "check", "--name",
                            f"Send the selected transaction information to {other_provider}", "--exact")
                    click("button", f"Ask {other_provider} for suggestions")
                    snap()
                    show_opinions(2 if other_provider == "Anthropic" else 3)
                    wait("AI opinions disagree")
                    click("button", f"Ask {other_provider} for suggestions")
                    snap()
                other_requests = [json.loads(line) for line in (scratch / "other-requests.jsonl").read_text().splitlines()]
                assert [r["provider"] for r in other_requests] == ["anthropic", "openai"]
                assert not any(r["network_attempted"] for r in other_requests)
                (output / "other-requests.json").write_text(json.dumps(other_requests, indent=2))
                show_panel()
                assert len([line for line in snap().splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line]) == 2
                show_opinions(3)
                command("screenshot", str(output / "provider-comparison.png"))
                checks += ["packaged Anthropic and OpenAI structured responses", "independent disagreement display", "second opinions preserve accepted category and exclusions", "second opinions reuse requests"]
                click("button", "Select all")
                second_chip = "Remove 2026-09-02 | Unknown marketplace no receipt | $-48.25"
                command("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => "
                        f"b.getAttribute('aria-label') === {json.dumps(second_chip)})")
                click("button", "Clear selection")
                command("wait", "--fn", "!Array.from(document.querySelectorAll('button')).some(b => "
                        f"b.getAttribute('aria-label') === {json.dumps(second_chip)})")
                show_panel()
                assert len([line for line in snap().splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line]) == 2
                checks.append("bulk select/clear preserves posting exclusion")
                click("button", "Save review for later")
                wait("Review saved in this encrypted book")
                # Include only the accepted first row through the actual checkbox.
                first = next(line for line in snap().splitlines() if 'checkbox "Include for posting"' in line)
                command("check", "@" + re.search(r"ref=(e\d+)", first)[1])
                click("button", "Post Transactions")
                wait("Posted 1 transaction")
                command("screenshot", str(output / "posted.png"))
                verified = subprocess.run([sys.executable, str(ROOT / "scripts/packaged_jev_fixture.py"),
                                           "--data-dir", str(scratch), "--verify"],
                                          capture_output=True, text=True, check=True, timeout=180)
                evidence = json.loads(verified.stdout)
                assert evidence["encrypted_header"]
                assert len(evidence["journal_entries"]) == len(evidence["imported_transactions"]) == 1
                assert sum(row["debit"] for row in evidence["journal_entry_lines"]) == 3333
                assert sum(row["credit"] for row in evidence["journal_entry_lines"]) == 3333
                assert evidence["imported_transactions"][0]["status"] == "Posted"
                imported = evidence["imported_transactions"][0]
                assert imported["row_fingerprint"] and imported["idempotency_key"] and imported["source_id"]
                assert imported["amount"] == -3333
                assert evidence["journal_entries"][0]["created_by"] and "(AI)" not in evidence["journal_entries"][0]["created_by"]
                assert evidence["audit_log"]
                (output / "database-evidence.json").write_text(json.dumps(evidence, indent=2))
                checks += ["human posting creates one balanced integer-cent entry", "excluded row is not posted",
                           "import identity and human audit attribution retained"]
                (output / "posting-result.json").write_text(json.dumps({"checks": checks, "requests": requests,
                    "source_sha256": hashes, "live": bool(args.key_file)}, indent=2))

                click("link", "Journal Entries", exact=False)
                click("radio", "View Entries")
                wait("Journal Entry List")
                snap()
                entry_header = "summary:has(strong)"  # The observed journal header contains bold entry #1.
                command("wait", entry_header)
                command("click", entry_header)
                wait("Balanced")
                command("eval", "Array.from(document.querySelectorAll('summary')).find(e => e.textContent.includes('Cedar Paper')).scrollIntoView({block:'start'})")
                command("wait", '[data-testid="stDataFrame"]')
                command("screenshot", str(output / "journal-table.png"))
                checks += ["CSV preview shows 81.58 disbursements", "journal entry displays a balanced debit/credit table"]

                click("link", "Trial Balance Worksheet", exact=False)
                wait("Export Excel")
                wait("Close Package (PDF)")
                wait("Close Package (Excel)")
                snap()
                assert "Exports could not be prepared" not in command("get", "text", "body")["text"]
                command("eval", "document.querySelector('.st-key-worksheet_actions').scrollIntoView({block:'center'})")
                command("mouse", "move", "10", "10")
                geometry = command("eval", "(() => {const bar=document.querySelector('.st-key-worksheet_actions'); return {overflow:document.documentElement.scrollWidth>innerWidth+1,buttons:Array.from(bar.querySelectorAll('button')).filter(b=>b.getBoundingClientRect().width>0).map(b=>{const p=b.querySelector('p')||b;const range=document.createRange();range.selectNodeContents(p);const r=b.getBoundingClientRect();return {label:b.textContent.trim(),lines:range.getClientRects().length,left:r.left,right:r.right,width:innerWidth};})};})()")['result']
                (output / "worksheet-action-geometry.json").write_text(json.dumps(geometry,indent=2))
                assert not geometry['overflow'] and len(geometry['buttons']) == 5, geometry
                assert all(b['lines'] == 1 and b['left'] >= 0 and b['right'] <= b['width'] + 1 for b in geometry['buttons']), geometry
                command("screenshot", str(output / "worksheet-exports.png"))
                checks.append("worksheet generates all three PDF/XLSX exports")
                checks.append("worksheet actions retain single-line labels and wrap within the viewport")
                click("link", "Chart of Accounts", exact=False)
                wait("4 accounts need a statement grouping.")
                snap()
                command("wait", "--fn", "Array.from(document.querySelectorAll('summary')).some(s=>s.textContent.includes('View accounts and assign groupings'))")
                collapsed = command("eval", "Array.from(document.querySelectorAll('details')).find(d=>d.querySelector('summary')?.textContent.includes('View accounts and assign groupings')).open")['result']
                assert collapsed is False
                command("screenshot", str(output / "grouping-collapsed.png"))
                command("find", "text", "View accounts and assign groupings (4)", "click")
                body = command("get", "text", "body")['text']
                for account in ('2000 — Credit Card', '4000 — Design Revenue', '6100 — Office Supplies', '6200 — Software'):
                    assert account in body
                command("screenshot", str(output / "grouping-expanded.png"))
                checks.append("account grouping warning collapses to a count and expands into an account list")
                click("link", "Import Transactions", exact=False)
                click("radio", "Review & Categorize")
                snap()
                command("eval", "Array.from(document.querySelectorAll('details')).filter(d => d.querySelector('summary')?.textContent.includes('Saved review ·')).forEach(d => {if(!d.open)d.querySelector('summary').click();})")
                click("button", "Resume saved review")
                wait("Saved review resumed")
                state=snap()
                assert len([line for line in state.splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line])==2
                assert len((scratch / "requests.jsonl").read_text().splitlines())==2
                command("screenshot", str(output / "saved-review-resumed.png"))
                checks.append("encrypted saved review resumes after posting without a request or inclusion change")

                # Both fixture books deliberately have client ID 1 and the same
                # account IDs. Switching must still reset consent/review state.
                fixture_books = json.loads((scratch / "synthetic-fixture.json").read_text())["books"]
                assert fixture_books[0]["client_id"] == fixture_books[1]["client_id"]
                click("link", "Data Safety", exact=False)
                click("button", "Switch book…")
                wait("Open a recent book")
                click("combobox", "Recent books")
                # Recent books stores canonical paths; macOS /var is a symlink
                # to /private/var even when tempfile returns the shorter form.
                click("option", str((scratch / "Books/Maple.ledgertb").resolve()))
                click("button", "Open selected")
                wait("Enter your passphrase")
                fill("Passphrase", "fictional-packaged-acceptance-only")
                click("button", "Unlock")
                click("link", "Import Transactions", exact=False)
                wait("Viewing: Maple Synthetic Studio")
                wait("Upload CSV file")
                command("upload", 'input[type="file"]', str(scratch / "synthetic-import.csv"))
                wait("Check totals")
                check("The account, columns and totals are correct")
                click("button", "Continue to review")
                wait("Review & Categorize Transactions")
                show_panel("AI suggestions")
                state = snap()
                assert 'checkbox "Send the selected transaction information to TypeSafe" [checked=false' in state
                assert "Accept account suggestion" not in state and 'button "Remove 2026-' not in state
                click("combobox", "Selected rows")
                click("option", "2026-09-01 | Cedar Paper receipt printer paper solely for design studio | $-33.33")
                command("press", "Tab")
                show_panel("AI suggestions")
                state = snap()
                assert "Accept account suggestion" not in state
                assert 'button "Ask Jev for suggestions" [disabled' in state
                assert len((scratch / "requests.jsonl").read_text().splitlines()) == 2
                verified = subprocess.run([sys.executable, str(ROOT / "scripts/packaged_jev_fixture.py"),
                                           "--data-dir", str(scratch), "--verify"],
                                          capture_output=True, text=True, check=True, timeout=180)
                switched = json.loads(verified.stdout)
                assert [(b["journal_entries"], b["imported_transactions"]) for b in switched["book_counts"]] == [(1, 1), (0, 0)]
                assert all(b["encrypted_header"] for b in switched["book_counts"])
                (output / "book-switch-evidence.json").write_text(json.dumps(switched["book_counts"], indent=2))
                command("screenshot", str(output / "book-switched.png"))
                checks += ["book switch with identical client/account IDs resets consent and review state",
                           "second book receives no Jev result or entries and makes no new provider request"]
                (output / "result.json").write_text(json.dumps({"checks": checks, "requests": requests,
                    "source_sha256": hashes, "live": bool(args.key_file), "mode": "packaged" if app else "source", "viewport": {"width": args.width, "height": args.height},
                    "limitations": "Chromium with test-only fake vault; not native window, installed upgrade or Windows acceptance. Source mode does not qualify packaging."}, indent=2))
                print(f"{'Packaged' if app else 'Source'} Jev workflow: {len(checks)} checks passed")
            except Exception:
                # Keep the original error even if a wedged browser cannot
                # provide diagnostics. These reads never replay an action.
                for parts in ([("snapshot", "-i"), ("screenshot", str(output / "failure.png"))] if page_opened else []):
                    try:
                        command(*parts)
                    except Exception:
                        pass
                raise
            finally:
                try:
                    subprocess.run(cli + ["close"], capture_output=True, timeout=20)
                except subprocess.TimeoutExpired:
                    pass
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                events = scratch / "requests.jsonl"
                if events.exists():
                    shutil.copyfile(events, output / "transport-events.jsonl")


if __name__ == "__main__":
    main()
