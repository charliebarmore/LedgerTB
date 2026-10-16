"""Browser regression checks for review text, Close Map, and local-model review.

Uses a disposable encrypted Cedar book and a loopback Ollama test endpoint.
The fake vault is process-local. No real book or provider credentials are used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-browser", default=str(ROOT / "tests/browser-tools/node_modules/.bin/agent-browser"))
    parser.add_argument("--chrome")
    parser.add_argument("--output", type=Path, default=ROOT / "output/review-browser")
    parser.add_argument("--expect-bugs", action="store_true")
    parser.add_argument("--app", type=Path, help="Disposable .app under output/; gets a test-only vault")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    if args.app:
        args.app = args.app.resolve()
        if not args.app.is_relative_to(ROOT / "output") or args.app.suffix != ".app":
            parser.error("Use a disposable .app under this checkout's output/")
        bundle = args.app / "Contents/Frameworks"
        if not bundle.resolve().is_relative_to(args.app):
            parser.error("Bundle source must stay inside the disposable app")
        for directory in ("pages", "models", "services", "utils", "database"):
            for source in (ROOT / directory).rglob("*"):
                if source.suffix not in (".py", ".sql"):
                    continue
                name = str(source.relative_to(ROOT))
                assert (bundle / name).read_bytes() == source.read_bytes(), f"Rebuild: {name} differs"
                hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(ROOT / "tests/helpers/review_fake_vault.py", bundle / "review_fake_vault.py")
        with (args.output / "signature.log").open("w") as log:
            subprocess.run(["codesign", "--force", "--deep", "--options", "runtime", "--sign", "-",
                            "--entitlements", str(ROOT / "scripts/entitlements.plist"), str(args.app)],
                           stdout=log, stderr=log, check=True, timeout=600)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.app)],
                           stdout=log, stderr=log, check=True, timeout=600)
    cli = [args.agent_browser, "--session", "review-" + uuid.uuid4().hex[:10], "--json"]
    if args.chrome:
        cli += ["--executable-path", args.chrome]
    transcript = []

    def command(*parts):
        result = subprocess.run(cli + list(parts), capture_output=True, text=True, timeout=45)
        transcript.append({"command": parts, "stdout": result.stdout, "stderr": result.stderr})
        (args.output / "browser.json").write_text(json.dumps(transcript, indent=2))
        assert result.returncode == 0, (parts, result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["success"], payload
        return payload["data"]

    def wait(text):
        command("wait", "--text", text)

    def settled():
        command("wait", '[data-testid="stApp"][data-test-script-state="notRunning"]')

    def click(role, name, exact=True):
        settled()
        command("find", "role", role, "click", "--name", name, *(["--exact"] if exact else []))

    def fill(name, value):
        # Labels also resolve password inputs, which have no implicit textbox role.
        settled()
        command("find", "label", name, "fill", value, "--exact")

    def body():
        settled()
        return command("get", "text", "body")["text"]

    def selected_account():
        return command("eval", "Array.from(document.querySelectorAll('h2,h3')).find(el => /^\\d+ — /.test(el.textContent))?.textContent")["result"]

    def show_panel(wanted=None):
        settled()
        state = command("snapshot", "-i")["snapshot"]
        for label in ("Select rows for actions", "AI suggestions", "Change category", "Sort"):
            opened = f'button "Close {label}"' in state
            if opened != (label == wanted):
                click("button", f"Close {label}" if opened else label)
                state = command("snapshot", "-i")["snapshot"]

    def check(name):
        command("find", "role", "checkbox", "check", "--name", name, "--exact")
        settled()

    def opinions():
        settled()
        command("eval", "Array.from(document.querySelectorAll('details')).filter(d => "
                "d.querySelector('summary')?.textContent.includes('AI opinions')).forEach(d => {"
                "if (!d.open) d.querySelector('summary').click(); })")

    with tempfile.TemporaryDirectory(prefix="ledgertb-review-") as scratch:
        book_dir = Path(scratch) / "book"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (args.output / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, str(ROOT / "scripts/browser_fixture.py"),
                                       "--data-dir", str(book_dir), "--port", str(port),
                                       "--review-regressions", *(["--app", str(args.app)] if args.app else [])],
                                      cwd=ROOT, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 90
                while True:
                    assert server.poll() is None, "Fixture exited; see server.log"
                    try:
                        with urlopen(base + "/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    assert time.monotonic() < deadline, "Fixture startup timed out"
                    time.sleep(.1)
                for route in ("/", "/_stcore/health"):
                    with urlopen(Request(base + route, headers={"Accept-Encoding": "gzip"}), timeout=10) as response:
                        assert response.status == 200 and response.read(), route
                fixture = json.loads((book_dir / "fixture.json").read_text())
                command("open", base + "/?t=cedar-browser-test")
                wait("Enter your passphrase")
                fill("Passphrase", "cedar-browser-passphrase")
                click("button", "Unlock")
                wait("Viewing: Cedar Demo Services")
                click("link", "Journal Entries", exact=False)
                wait("Create Journal Entry")
                click("radio", "Drafts")
                wait("Synthetic currency explanation")
                settled()
                draft_math = command("eval", "document.querySelectorAll('.katex, .katex-error, [data-testid=stMarkdownContainer] .math-inline').length")["result"]
                draft_text = body()
                command("eval", "Array.from(document.querySelectorAll('[data-testid=stMarkdownContainer]')).find(el => el.textContent.startsWith('Why: Accrue'))?.scrollIntoView({block:'center'})")
                command("screenshot", str(args.output / "draft.png"))

                click("link", "Close Map", exact=False)
                wait("Review account")
                click("combobox", "Review account")
                click("option", "3000 — Owner Capital", exact=False)
                wait("Capital moved")
                settled()
                proposal_math = command("eval", "document.querySelectorAll('.katex, .katex-error, [data-testid=stMarkdownContainer] .math-inline').length")["result"]
                proposal_text = body()
                command("eval", "Array.from(document.querySelectorAll('[data-testid=stAlert]')).find(el => el.textContent.includes('Capital moved'))?.scrollIntoView({block:'center'})")
                command("screenshot", str(args.output / "proposal.png"))
                click("button", "Mark prepared")
                wait("Latest preparer signoff")
                settled()
                after_prepare = selected_account()
                click("button", "Mark reviewed")
                settled()
                after_review = selected_account()
                review_text = body()
                command("screenshot", str(args.output / "close-map.png"))

                os.environ["LEDGERTB_DB_PATH"] = fixture["book"]
                from database import connection as dbc
                from database.crypto import derive_key
                dbc.set_active_key(derive_key("cedar-browser-passphrase"))
                with dbc.get_cursor() as cur:
                    signoffs = [dict(row) for row in cur.execute(
                        "SELECT r.account_id, s.role FROM account_close_signoffs s "
                        "JOIN account_close_reviews r ON r.id=s.review_id ORDER BY s.id")]
                dbc.clear_active_key()
                result = {"draft_math_nodes": draft_math, "proposal_math_nodes": proposal_math,
                          "after_prepare": after_prepare, "after_review": after_review,
                          "signoffs": signoffs}
                (args.output / "result.json").write_text(json.dumps(result, indent=2))
                if args.expect_bugs:
                    assert "Accrue $1,200.00 of rent against $1,200.00 payable." not in draft_text
                    assert "Capital moved from $0.00 to $10,000.00." not in proposal_text
                    assert not any(row["role"] == "reviewer" for row in signoffs), result
                    print("Reproduced both demo defects")
                    return
                assert draft_math == proposal_math == 0, result
                assert "Accrue $1,200.00 of rent against $1,200.00 payable." in draft_text
                assert "Capital moved from $0.00 to $10,000.00." in proposal_text
                assert "3000 — Owner Capital" in after_prepare and "3000 — Owner Capital" in after_review, result
                assert "Latest reviewer signoff" in review_text
                assert signoffs == [{"account_id": fixture["accounts"]["capital"], "role": role}
                                    for role in ("preparer", "reviewer")], result
                # Keyboard selection follows the same prepare/review path on a
                # second non-first account. A refresh proves the saved result.
                click("combobox", "Review account")
                command("press", "ArrowDown")
                command("press", "Enter")
                wait("4000 — Service Revenue")
                settled()
                assert selected_account() == "4000 — Service Revenue"
                click("button", "Mark prepared")
                wait("Latest preparer signoff")
                click("button", "Mark reviewed")
                wait("Latest reviewer signoff")
                assert selected_account() == "4000 — Service Revenue"
                command("reload")
                wait("Review account")
                click("combobox", "Review account")
                click("option", "4000 — Service Revenue")
                wait("Latest reviewer signoff")

                click("link", "Firm Settings", exact=False)
                wait("Default categorization provider")
                click("combobox", "Default categorization provider")
                click("option", "Local model (Ollama)")
                click("button", "Save categorization provider")
                wait("Provider saved")
                fill("Local model server address", "http://example.com")
                click("button", "Save local model address")
                wait("Plain http works only")
                fill("Local model server address", fixture["local_url"] + "/")
                click("button", "Save local model address")
                wait("Local model address saved")
                command("screenshot", str(args.output / "local-settings.png"))
                assert json.loads((book_dir / "local-requests.json").read_text()) == []
                click("link", "Import Transactions", exact=False)
                wait("Upload CSV file")
                command("upload", 'input[type="file"]', str(book_dir / "review.csv"))
                wait("Check totals")
                check("The account, columns and totals are correct")
                click("button", "Continue to review")
                wait("Review & Categorize Transactions")
                click("button", "Exclude All")
                show_panel("Select rows for actions")
                click("button", "Select all")
                show_panel("AI suggestions")
                assert "Local model (Ollama)" in body()
                assert json.loads((book_dir / "local-requests.json").read_text()) == []
                state = command("snapshot", "-i")["snapshot"]
                assert any('button "Ask Local model (Ollama) for suggestions"' in line and "disabled" in line
                           for line in state.splitlines()), state
                check("Send the selected transaction information to Local model (Ollama)")
                click("button", "Ask Local model (Ollama) for suggestions")
                show_panel()
                opinions()
                wait("Synthetic receipt identifies office supplies.")
                click("button", "Accept Local model (Ollama) suggestion")
                # Streamlit 1.66 renders the selection as an input value, which
                # agent-browser's interactive snapshot omits. Older versions
                # render text beside the input. Scope the check to this picker,
                # never the opinion text (which already names the account).
                command("wait", "--fn", "(() => {"
                        "const input = document.querySelector('[role=combobox][aria-label=Account]');"
                        "return input && (input.value === '6000 - Office Expense' || "
                        "input.closest('[data-testid=stSelectbox]')?.textContent.includes('6000 - Office Expense'));"
                        "})()")
                settled()
                state = command("snapshot", "-i")["snapshot"]
                assert 'checkbox "Include for posting" [checked=false' in state, state
                show_panel("AI suggestions")
                click("button", "Ask Local model (Ollama) for suggestions")
                settled()
                assert len(json.loads((book_dir / "local-requests.json").read_text())) == 1
                show_panel("AI suggestions")
                click("combobox", "Model")
                click("option", "Other model ID")
                fill("Model ID", "missing-demo-model")
                command("press", "Tab")
                settled()
                state = command("snapshot", "-i")["snapshot"]
                assert 'checkbox "Send the selected transaction information to Local model (Ollama)" [checked=false' in state
                check("Send the selected transaction information to Local model (Ollama)")
                click("button", "Ask Local model (Ollama) for suggestions")
                show_panel()
                opinions()
                wait("does not have that model")
                assert len(json.loads((book_dir / "local-requests.json").read_text())) == 2
                state = command("snapshot", "-i")["snapshot"]
                assert 'checkbox "Include for posting" [checked=false' in state
                assert "6000 - Office Expense" in state
                command("eval", "Array.from(document.querySelectorAll('[data-testid=stAlert]')).find(el => el.textContent.includes('does not have that model'))?.scrollIntoView({block:'center'})")
                command("screenshot", str(args.output / "local-missing-model.png"))
                dbc.set_active_key(derive_key("cedar-browser-passphrase"))
                with dbc.get_cursor() as cur:
                    final_signoffs = [dict(row) for row in cur.execute(
                        "SELECT r.account_id, s.role FROM account_close_signoffs s "
                        "JOIN account_close_reviews r ON r.id=s.review_id ORDER BY s.id")]
                    assert final_signoffs == [
                        {"account_id": fixture["accounts"][account], "role": role}
                        for account in ("capital", "revenue") for role in ("preparer", "reviewer")]
                    assert cur.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 3
                    assert cur.execute("SELECT COUNT(*) FROM draft_entries WHERE status='pending'").fetchone()[0] == 2
                    recovery = cur.execute("SELECT payload FROM import_review_recovery").fetchall()
                    assert len(recovery) == 1
                    saved_rows = json.loads(recovery[0]["payload"])["rows"]
                    assert len(saved_rows) == 1
                    expense = cur.execute("SELECT id FROM accounts WHERE account_number='6000'").fetchone()[0]
                    assert saved_rows[0]["selected_account_id"] == expense
                    assert saved_rows[0]["include"] is False
                dbc.clear_active_key()
                if args.app:
                    assert (book_dir / "fake-vault-loaded").exists()
                result.update(status="passed", signoffs=final_signoffs, local_requests=2,
                              mode="packaged" if args.app else "source", source_sha256=hashes)
                (args.output / "result.json").write_text(json.dumps(result, indent=2))
                print("Review browser checks passed")
            except BaseException:
                try:
                    (args.output / "failure.txt").write_text(body())
                    command("screenshot", str(args.output / "failure.png"))
                except Exception:
                    pass
                raise
            finally:
                try:
                    command("close")
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)


if __name__ == "__main__":
    main()
