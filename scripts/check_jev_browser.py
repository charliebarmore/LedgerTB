"""Reproduce Jev review state regressions in an isolated real browser.

Uses scripts/jev_browser_fixture.py: fake vault/provider, temporary encrypted book.
No real ledger, credential or cloud request is used.
"""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-browser", default=str(ROOT / "tests/browser-tools/node_modules/.bin/agent-browser"))
    parser.add_argument("--output", type=Path, default=ROOT / "output/jev-browser-review")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session = "jev-review-" + uuid.uuid4().hex[:10]
    namespace = "jv-" + session.rsplit("-", 1)[1][:8]
    cli = [args.agent_browser, "--namespace", namespace, "--session", session, "--pin-tab"]
    transcript = []

    def command(*parts):
        result = subprocess.run(cli + list(parts), capture_output=True, text=True, timeout=40)
        transcript.append({"command": list(parts), "stdout": result.stdout, "stderr": result.stderr})
        (args.output / "browser.json").write_text(json.dumps(transcript, indent=2))
        assert result.returncode == 0, result.stderr
        return result.stdout

    def show_panel(wanted=None):
        state = snapshot()
        for label in ("Select rows for actions", "AI suggestions", "Change category", "Sort"):
            opened = f'button "Close {label}"' in state
            if opened != (label == wanted):
                command("find", "role", "button", "click", "--name", f"Close {label}" if opened else label, "--exact")
                state = snapshot()

    def show_opinions(count=1, failed=False):
        command("wait", "--fn", "Array.from(document.querySelectorAll('summary')).some(s => "
                f"s.textContent.includes('AI opinions ({count})') && s.textContent.includes('Request failed') === {str(failed).lower()})")
        # Open the visible per-row disclosure; no application state is injected.
        command("eval", "Array.from(document.querySelectorAll('details')).filter(d => "
                "d.querySelector('summary')?.textContent.includes('AI opinions')).forEach(d => {"
                "if (!d.open) d.querySelector('summary').click(); })")


    def click(role, name):
        if name in ("Selected rows", "Select all", "Clear selection"):
            show_panel("Select rows for actions")
        elif name in ("Ask Jev for suggestions", "Retry failed Jev requests", "Choose another AI", "Ask Anthropic for suggestions", "Ask OpenAI for suggestions", "AI provider", "Model"):
            show_panel("AI suggestions")
        elif role != "option":
            show_panel()
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
        command("find", "role", role, "click", "--name", name, "--exact")

    def settled():
        command("wait", '[data-testid="stApp"][data-test-script-state="notRunning"]')

    def snapshot():
        settled()
        return command("snapshot", "-i")

    def check_account(selected):
        state = snapshot()
        assert re.search(r'checkbox "Include for posting".*checked=false', state), state
        account = next(line for line in state.splitlines() if 'combobox "Account"' in line)
        assert ("6100 - Office Supplies" in account) == selected, account

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (args.output / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, str(ROOT / "scripts/jev_browser_fixture.py"),
                                   "--port", str(port)], stdout=log, stderr=log, cwd=ROOT)
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if server.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("Fixture failed to start; see server.log")
                time.sleep(.1)
            command("set", "viewport", str(args.width), str(args.height))
            command("open", f"http://127.0.0.1:{port}")
            command("wait", "--text", "Select rows for actions")
            snapshot()
            click("combobox", "Selected rows")
            command("snapshot", "-i")
            click("option", "2026-01-01 | Cedar Paper: printer paper receipt | $-33.33")
            # Move focus out of the menu; Escape can stop Streamlit, while
            # React Aria's accessibility-only Dismiss button is not clickable.
            command("press", "Tab")
            snapshot()
            show_panel("AI suggestions")
            command("find", "role", "checkbox", "check", "--name", "Send the selected transaction information to TypeSafe", "--exact")
            snapshot()
            click("button", "Ask Jev for suggestions")
            snapshot()
            show_opinions()
            command("wait", "--text", "Suggested account:")
            check_account(False)
            click("button", "Accept account suggestion")
            command("wait", "--text", "Account accepted.")
            check_account(True)
            click("button", "Ask Jev for suggestions")
            snapshot()
            assert "Synthetic transport calls: 1" in command("get", "text", "body")
            click("button", "Choose another AI")
            state = snapshot()
            assert any('combobox "AI provider"' in line and 'Anthropic' in line for line in state.splitlines()), state
            assert 'Synthetic other-provider calls: 0' in command("get", "text", "body")
            command("find", "role", "checkbox", "check", "--name", "Send the selected transaction information to Anthropic", "--exact")
            click("button", "Ask Anthropic for suggestions")
            snapshot()
            show_opinions(2)
            command("wait", "--text", "AI opinions disagree")
            check_account(True)
            assert 'Synthetic other-provider calls: 1' in command("get", "text", "body")
            click("button", "Ask Anthropic for suggestions")
            snapshot()
            assert 'Synthetic other-provider calls: 1' in command("get", "text", "body")
            click("button", "Choose another AI")
            snapshot()
            command("find", "role", "checkbox", "check", "--name", "Send the selected transaction information to OpenAI", "--exact")
            click("button", "Ask OpenAI for suggestions")
            command("wait", "--text", "Synthetic other-provider calls: 2")
            check_account(True)
            show_panel("AI suggestions")
            command("find", "role", "combobox", "fill", "gpt-4o-mini", "--name", "Model", "--exact")
            snapshot()
            command("press", "ArrowDown")
            command("press", "Enter")
            state = snapshot()
            assert any('checkbox "Send the selected transaction information to OpenAI"' in line and 'checked=false' in line for line in state.splitlines()), state
            assert 'Synthetic other-provider calls: 2' in command("get", "text", "body")
            command("find", "role", "combobox", "fill", "gpt-4o-mini", "--name", "Model", "--exact")
            command("press", "Tab")
            geometry = command("eval", "(() => { const p = document.querySelector('.st-key-review_panel_ai'); "
                               "const r = p.getBoundingClientRect(); return JSON.stringify({left:r.left,right:r.right,width:innerWidth,"
                               "overflow:document.documentElement.scrollWidth>innerWidth+1}); })()")
            (args.output / "panel-geometry.json").write_text(geometry)
            geometry = json.loads(json.loads(geometry)) if isinstance(json.loads(geometry),str) else json.loads(geometry)
            assert geometry['left']>=0 and geometry['right']<=geometry['width']+1 and not geometry['overflow'], geometry
            command("screenshot", str((args.output / "provider-picker.png").resolve()))
            show_panel()
            show_opinions(3)
            command("screenshot", str((args.output / "comparison.png").resolve()))
            click("radio", "Upload CSV")
            command("wait", "--text", "Upload Bank/Credit Card CSV File")
            snapshot()
            click("radio", "Review & Categorize")
            command("wait", "--text", "Select rows for actions")
            check_account(True)
            show_panel()
            command("find", "role", "checkbox", "check", "--name", "Disable Jev provider", "--exact")
            show_panel("AI suggestions")
            command("wait", "--text", "AI categorization is off")
            snapshot()
            click("button", "Change synthetic evidence")
            command("wait", "--text", "Fixture evidence revision: 1")
            check_account(False)
            provider_toggle = next(line for line in snapshot().splitlines()
                                   if 'checkbox "Disable Jev provider"' in line)
            command("uncheck", "@" + re.search(r"ref=(e\d+)", provider_toggle).group(1))
            command("wait", "--text", "Select rows for actions")
            snapshot()
            click("combobox", "Selected rows")
            command("snapshot", "-i")
            click("option", "2026-01-01 | Cedar Paper: printer paper receipt changed | $-33.33")
            command("press", "Tab")
            snapshot()
            show_panel("AI suggestions")
            command("find", "role", "checkbox", "check", "--name", "Send the selected transaction information to TypeSafe", "--exact")
            show_panel()
            command("find", "role", "checkbox", "check", "--name", "Simulate network timeout", "--exact")
            command("wait", "--text", "Synthetic failure mode: on")
            snapshot()
            click("button", "Ask Jev for suggestions")
            snapshot()
            show_opinions(failed=True)
            command("wait", "--text", "could not be reached")
            state = snapshot()
            retry = next(line for line in state.splitlines() if 'button "Retry failed Jev requests"' in line)
            assert "disabled" not in retry, retry
            assert "Synthetic transport calls: 2" in command("get", "text", "body")
            check_account(False)
            click("button", "Retry failed Jev requests")
            command("wait", "--text", "Synthetic transport calls: 3")
            check_account(False)
            command("screenshot", str((args.output / "final.png").resolve()))
            click("button", "Save review for later")
            command("wait", "--text", "Review saved in this encrypted book")
            click("button", "Clear review list")
            command("wait", "--text", "No transactions to review")
            command("eval", "Array.from(document.querySelectorAll('details')).filter(d => d.querySelector('summary')?.textContent.includes('Saved review ·')).forEach(d => {if(!d.open)d.querySelector('summary').click();})")
            click("button", "Resume saved review")
            command("wait", "--text", "Saved review resumed")
            check_account(False)
            assert "Synthetic transport calls: 3" in command("get", "text", "body")
            command("screenshot", str((args.output / "saved-review.png").resolve()))
            (args.output / "result.json").write_text(json.dumps({"passed": True, "checks": [
                "consent", "separate inclusion", "acceptance", "request reuse", "navigation persistence",
                "provider-off invalidation", "failure preservation", "immediate explicit retry",
                "independent Anthropic opinion", "OpenAI opinion", "disagreement visibility", "second-opinion reuse", "model switch resets consent without a request", "panel fits viewport without horizontal overflow", "saved review resumes without a cloud call"
            ]}, indent=2) + "\n")
            print("Jev browser review: 15 checks passed.")
        except Exception:
            # Preserve the rendered state for diagnosing assertion or timing failures.
            command("snapshot", "-i")
            command("screenshot", str((args.output / "failure.png").resolve()))
            raise
        finally:
            try:
                subprocess.run(cli + ["close"], capture_output=True, timeout=15)
            except subprocess.TimeoutExpired:
                pass
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
