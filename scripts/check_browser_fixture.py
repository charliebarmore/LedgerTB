"""Repeatable real-browser acceptance using agent-browser 0.36.0.

Creates its own temporary book, fake vault, server, and browser session. Pass
--agent-browser /path/to/agent-browser and optionally --chrome /path/to/chrome.
"""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-browser", default="agent-browser")
    parser.add_argument("--chrome")
    parser.add_argument("--output", type=Path, default=ROOT / "output/browser-acceptance")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    session = "cedar-" + uuid.uuid4().hex[:12]
    cli = [args.agent_browser, "--session", session, "--json"]
    if args.chrome:
        cli += ["--executable-path", args.chrome]
    transcript = []

    def command(*parts):
        result = subprocess.run(cli + list(parts), capture_output=True, text=True, timeout=40)
        transcript.append({"command": list(parts), "stdout": result.stdout, "stderr": result.stderr})
        (output / "browser.json").write_text(json.dumps(transcript, indent=2))
        if result.returncode:
            raise AssertionError(f"Browser command {parts} failed: {result.stdout} {result.stderr}")
        payload = json.loads(result.stdout)
        assert payload["success"], payload
        return payload["data"]

    def snapshot():
        return command("snapshot", "-i")["snapshot"]

    def ref(role, name, contains=False):
        # Streamlit sends headings before later widgets. Wait for the actual
        # control rather than treating an early heading as a finished render.
        deadline = time.monotonic() + 10
        while True:
            state = snapshot()
            matches = []
            for line in state.splitlines():
                match = re.search(r'- ' + re.escape(role) + r' "([^"]+)".*?\bref=(e\d+)', line)
                if match and (name in match[1] if contains else name == match[1]):
                    matches.append("@" + match[2])
            if len(matches) == 1:
                return matches[0]
            assert len(matches) < 2 and time.monotonic() < deadline, (role, name, matches, state)
            time.sleep(0.1)

    def click(role, name, contains=False):
        ref(role, name, contains)  # Wait until the control is rendered.
        # Resolve by name at action time: a Streamlit rerun can replace DOM
        # nodes between a snapshot and click, making numbered refs stale.
        options = [] if contains else ["--exact"]
        command("find", "role", role, "click", "--name", name, *options)

    def fill(role, name, value):
        ref(role, name)
        command("find", "role", role, "fill", value, "--name", name, "--exact")

    def wait(text):
        command("wait", "--text", text)

    def body():
        return command("get", "text", "body")["text"]

    def settled():
        command("wait", '[data-testid="stApp"][data-test-script-state="notRunning"]')

    with tempfile.TemporaryDirectory(prefix="ledgertb-browser-") as scratch:
        book_dir = Path(scratch) / "book"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with (output / "server.log").open("w") as log:
            server = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/browser_fixture.py"),
                 "--data-dir", str(book_dir), "--port", str(port)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None, "Fixture server exited; see server.log"
                    try:
                        with urlopen(base + "/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    assert time.monotonic() < deadline, "Fixture did not start"
                    time.sleep(0.1)

                # Unauthorized windows remain blocked and receive no launch token.
                command("open", base)
                wait("This page was not opened by LedgerTB")
                assert "t=" not in command("get", "url")["url"]
                command("open", base + "/?t=cedar-browser-test")
                wait("Enter your passphrase")
                fill("textbox", "Passphrase", "cedar-browser-passphrase")
                click("button", "Unlock")
                wait("Viewing: Cedar Demo Services")
                click("link", "Journal Entries", contains=True)
                wait("Create Journal Entry")
                fill("textbox", "Description", "Cedar unsaved entry")
                click("combobox", "Select Client")
                click("option", "Maple Empty Demo")
                wait("Viewing: Maple Empty Demo")
                assert command("get", "value", ref("textbox", "Description"))["value"] == ""
                assert "Use template" not in snapshot()
                click("combobox", "Select Client")
                click("option", "Cedar Demo Services")
                wait("Viewing: Cedar Demo Services")
                assert command("get", "value", ref("textbox", "Description"))["value"] == ""

                click("radio", "Templates & recurring")
                wait("Recurring schedule")
                settled()
                command("find", "text", "Recurring schedule", "click")
                reversal_label = "Create a reversal draft after the period-end entry posts"
                command("wait", "--fn", "Array.from(document.querySelectorAll('input[type=checkbox]')).some(el => "
                        "el.closest('label')?.innerText.includes('Create a reversal draft after the period-end entry posts') "
                        "&& el.checked)")
                click("checkbox", reversal_label)
                command("wait", "--fn", "Array.from(document.querySelectorAll('input[type=checkbox]')).some(el => "
                        "el.closest('label')?.innerText.includes('Create a reversal draft after the period-end entry posts') "
                        "&& !el.checked)")
                settled()
                # Date inputs can restore focus and open their calendar during
                # a rerun. Dismiss it as a user would before clicking Save;
                # never force a click through an overlapping calendar.
                command("press", "Escape")
                command("wait", "--fn", "!Array.from(document.querySelectorAll('[role=heading], h2')).some(el => "
                        "el.textContent.startsWith('Choose date,'))")
                click("button", "Save schedule")
                wait("Schedule for Monthly rent accrual saved.")
                click("radio", "Drafts")
                wait("Approval will also create a separate reversal draft")
                click("button", "Approve & post")
                wait("Its reversal draft now awaits review.")
                command("reload")
                wait("Create Journal Entry")
                assert "t=cedar-browser-test" in command("get", "url")["url"]

                click("link", "Dashboard", contains=True)
                wait("Fiscal YTD Net Income")
                dashboard = body()
                # Cedar is deliberately a fixed 2026 book. Later fiscal years
                # carry its prior earnings in equity, with zero current income.
                net_income = "$1,000.00" if date.today().year == 2026 else "$0.00"
                assert all(value in dashboard for value in ("$12,200.00", "$1,200.00", net_income))
                command("screenshot", str(output / "cedar-dashboard.png"))
                command("back")
                wait("Create Journal Entry")
                click("radio", "Drafts")
                wait("Reversal: January rent accrual")
                click("button", "Approve & post")
                wait("No drafts waiting for review.")
                command("reload")
                wait("Create Journal Entry")

                # Check durable state independently of the browser's success text.
                fixture = json.loads((book_dir / "fixture.json").read_text())
                os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"  # pragma: allowlist secret -- synthetic fixture, never sent
                os.environ["LEDGERTB_DB_PATH"] = fixture["book"]
                from database import connection as dbc
                from database.crypto import derive_key
                dbc.set_active_key(derive_key("cedar-browser-passphrase"))
                with dbc.get_cursor() as cur:
                    assert cur.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 5
                    assert cur.execute("SELECT COUNT(*) FROM draft_entries WHERE status='pending'").fetchone()[0] == 0
                    assert cur.execute("SELECT COUNT(*) FROM recurring_occurrence_drafts").fetchone()[0] == 2
                    assert cur.execute("SELECT reversal_rule FROM recurring_schedules").fetchone()[0] == "None"
                    assert cur.execute("SELECT snapshot_reversal_rule FROM recurring_occurrence_drafts WHERE role='Primary'").fetchone()[0] == "NextDay"
                    assert cur.execute("SELECT COUNT(*) FROM journal_entries WHERE client_id=?",
                                       (fixture["other_client_id"],)).fetchone()[0] == 0
                dbc.clear_active_key()
                assert "Traceback" not in (output / "server.log").read_text()
                (output / "failure.png").unlink(missing_ok=True)
                (output / "result.json").write_text(json.dumps({"status": "passed", "posted_entries": 5,
                    "checks": ["unauthorized gate", "passphrase unlock", "client isolation", "saved reversal instruction",
                               "approval", "refresh", "dashboard amounts", "back navigation", "reversal approval"]}, indent=2))
                print(f"Browser acceptance passed; evidence: {output}")
            except BaseException:
                try:
                    command("screenshot", str(output / "failure.png"))
                except Exception:
                    pass
                (output / "result.json").write_text(json.dumps({"status": "failed"}))
                raise
            finally:
                try:
                    command("close")
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)


if __name__ == "__main__":
    main()
