"""Exercise the real GTK source launcher with a disposable encrypted book.

Run in a Linux desktop session, or under xvfb-run in CI. Credential storage is
replaced in the child process; this never reads or writes the operating-system
vault. Native file dialogs and real credential persistence need a device pilot.
"""
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if sys.platform != "linux":
        raise SystemExit("This smoke check requires Linux with GTK and WebKitGTK.")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("Run in a desktop session or use xvfb-run -a.")

    with tempfile.TemporaryDirectory(prefix="ledgertb-linux-smoke-") as scratch:
        root = Path(scratch)
        os.environ.update(
            LEDGERTB_DB_PATH=str(root / "pilot.ledgertb"),
            LEDGERTB_DATA_DIR=str(root),
            LEDGERTB_BACKUP_DIR=str(root / "backups"),
            LEDGERTB_FIXTURE_DIR=str(root),
            PYTHON_DOTENV_DISABLED="1",
            PYTHON_KEYRING_BACKEND="review_fake_vault.Keyring",
            PYTHONPATH=str(ROOT / "tests/helpers"),
        )
        sys.path.insert(0, str(ROOT / "tests/helpers"))
        (root / "fixture.json").write_text("{}")
        from database import connection as dbc
        from database.crypto import derive_key
        from models.client import Client

        assert dbc.ENCRYPTION_AVAILABLE, "SQLCipher is required"
        dbc.set_active_key(derive_key("linux-smoke-passphrase"))
        dbc.init_database()
        Client(name="Linux Pilot Co", entity_type="S-Corp").save()
        dbc.clear_active_key()
        assert not (root / "pilot.ledgertb").read_bytes().startswith(b"SQLite format 3")

        import desktop
        processes = []
        failures = []
        completed = []
        original_start = desktop.start_streamlit

        def start(*args):
            process = original_start(*args)
            processes.append(process)
            return process

        def exercise(window, *_):
            def wait_for(expression):
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if window.evaluate_js(expression):
                        return
                    time.sleep(.2)
                raise AssertionError("GTK page did not reach the expected state")

            try:
                wait_for("document.body.innerText.includes('Enter your passphrase') && "
                         "document.querySelector('input[type=password]') instanceof HTMLInputElement && "
                         "Array.from(document.querySelectorAll('button')).some(b => b.innerText.trim() === 'Unlock')")
                window.evaluate_js("""(() => {
                    const input = document.querySelector('input[type=password]');
                    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                        .set.call(input, 'linux-smoke-passphrase');
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.blur();
                    Array.from(document.querySelectorAll('button'))
                        .find(button => button.innerText.trim() === 'Unlock').click();
                })()""")
                wait_for("document.body.innerText.includes('Viewing: Linux Pilot Co')")
                assert not window.evaluate_js("Boolean(document.querySelector('[data-testid=stException]'))")
                completed.append(True)
            except Exception as error:
                failures.append(str(error))
            finally:
                window.destroy()

        desktop.start_streamlit = start
        desktop._place_window = exercise
        assert desktop.main() == 0
        assert completed == [True], failures
        assert processes and all(process.poll() is not None for process in processes)
        assert (root / "fake-vault-loaded").exists(), "Test vault was not loaded"
        print("LINUX GTK SMOKE OK: encrypted unlock, native render, window close, server exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
