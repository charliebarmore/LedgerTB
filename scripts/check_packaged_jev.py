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
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--fixture-template", type=Path,
                        help="Copy a previously prepared synthetic fixture under output/ into a new disposable directory")
    parser.add_argument("--startup-timeout", type=float, default=180,
                        help="Diagnostic startup allowance; increasing it does not establish startup performance")
    parser.add_argument("--output", type=Path, default=ROOT / "output/packaged-jev-check")
    parser.add_argument("--agent-browser", default=str(ROOT / "tests/browser-tools/node_modules/.bin/agent-browser"))
    args = parser.parse_args()
    app = args.app.resolve()
    if not app.is_relative_to(ROOT / "output") or app.suffix != ".app":
        parser.error("Use a disposable .app under this checkout's output directory")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = app / "Contents/Frameworks"
    if not bundle.resolve().is_relative_to(app):
        parser.error("The disposable bundle must not link its Frameworks directory outside the app")
    sources = ["config.py", "pages/4_Import_Transactions.py", "services/jev_categorization.py",
               "utils/jev_review.py", "utils/import_review.py"]
    hashes = {}
    for name in sources:
        actual = (bundle / name).read_bytes()
        assert actual == (ROOT / name).read_bytes(), f"Rebuild: packaged {name} differs from checkout"
        hashes[name] = hashlib.sha256(actual).hexdigest()
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
    cli = [args.agent_browser, "--session", session, "--json"]
    transcript, checks = [], []

    def command(*parts):
        # Repeat only read-only waits on a contended machine. Never replay a
        # click, fill, upload, posting action or cloud request after a timeout.
        for attempt in range(6 if parts[0] == "wait" else 1):
            result = subprocess.run(cli + list(parts), capture_output=True, text=True, timeout=120)
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

    def click(role, name, exact=True):
        snap()
        if role == "button":
            command("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => "
                    f"(b.textContent.trim() === {json.dumps(name)} || b.getAttribute('aria-label') === {json.dumps(name)}) && !b.disabled)")
        command("find", "role", role, "click", "--name", name, *(["--exact"] if exact else []))

    def wait(text):
        command("wait", "--text", text)

    def fill(name, value):
        snap()
        command("find", "role", "textbox", "fill", value, "--name", name, "--exact")

    def check(name):
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
        env.pop("LEDGERTB_FIXTURE_KEY_FILE", None)
        if args.key_file:
            env["LEDGERTB_FIXTURE_KEY_FILE"] = str(args.key_file.expanduser().resolve())
        with (output / "server.log").open("w") as log:
            server = subprocess.Popen([str(app / "Contents/MacOS/LedgerTB")], env=env,
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
                command("open", base + "/?t=" + session)
                wait("Enter your passphrase")
                assert (scratch / "fake-vault-loaded").exists()
                fill("Passphrase", "fictional-packaged-acceptance-only")
                click("button", "Unlock")
                wait("Viewing: Cedar Synthetic Studio")
                click("link", "Import Transactions", exact=False)
                wait("Upload CSV file")
                command("upload", 'input[type="file"]', str(scratch / "synthetic-import.csv"))
                wait("Confirm Import")
                check("I have reviewed the CSV data and column mappings above and confirm they are correct")
                click("button", "Parse Transactions")
                wait("Review & Categorize Transactions")
                click("button", "Exclude All")
                click("combobox", "Rows to ask Jev about")
                click("option", "2026-09-01 | Cedar Paper receipt printer paper solely for design studio | $-33.33")
                command("press", "Tab")
                check("Send the selected transaction information to TypeSafe")
                (scratch / "offline").touch()
                click("button", "Ask Jev for suggestions")
                wait("TypeSafe could not be reached")
                assert snap().count('checkbox "Include for posting"') == 2
                click("button", "Ask Jev for suggestions")
                snap()
                failed_requests = [json.loads(line) for line in (scratch / "requests.jsonl").read_text().splitlines()]
                assert len(failed_requests) == 1 and not failed_requests[0]["network_attempted"]
                (scratch / "offline").unlink()
                click("button", "Retry failed Jev requests")
                wait("Suggested account:")
                click("button", "Accept account suggestion")
                wait("Account accepted.")
                assert snap().count('checkbox "Include for posting"') == 2
                assert len([line for line in snap().splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line]) == 2
                click("button", "Ask Jev for suggestions")
                snap()
                requests = [json.loads(line) for line in (scratch / "requests.jsonl").read_text().splitlines()]
                assert len(requests) == 2 and requests[1]["success"]
                assert sum(row["network_attempted"] for row in requests) == int(bool(args.key_file))
                checks += ["packaged SQLCipher unlock and CSV import", "explicit Jev request and human acceptance", "acceptance preserves exclusion", "rerun and repeated request reuse"]
                checks += ["offline failure preserves staged rows", "failed result reuse and explicit recovery retry"]
                click("button", "Select all for bulk")
                second_chip = "Remove 2026-09-02 | Unknown marketplace no receipt | $-48.25"
                command("wait", "--fn", "Array.from(document.querySelectorAll('button')).some(b => "
                        f"b.getAttribute('aria-label') === {json.dumps(second_chip)})")
                click("button", "Clear bulk selection")
                command("wait", "--fn", "!Array.from(document.querySelectorAll('button')).some(b => "
                        f"b.getAttribute('aria-label') === {json.dumps(second_chip)})")
                assert len([line for line in snap().splitlines() if 'checkbox "Include for posting"' in line and "checked=false" in line]) == 2
                checks.append("bulk select/clear preserves posting exclusion")
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
                (output / "result.json").write_text(json.dumps({"checks": checks, "requests": requests,
                    "source_sha256": hashes, "live": bool(args.key_file),
                    "limitations": "Frozen server mode in Chromium with test-only fake vault; not native window, installed upgrade or Windows acceptance."}, indent=2))
                print(f"Packaged Jev workflow: {len(checks)} checks passed")
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
