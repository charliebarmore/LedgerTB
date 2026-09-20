"""Native close warning channel: booleans only, no ledger data or credentials."""
import json
import os
from pathlib import Path
import tempfile
import threading

ENV = 'LEDGERTB_REVIEW_STATUS'
_lock = threading.Lock()
_windows = {}


def create_channel():
    directory = tempfile.TemporaryDirectory(prefix='ledgertb-review-status-')
    path = Path(directory.name) / 'status.json'
    path.write_text('{"review_open": false}')
    return directory, path


def publish(window_id, has_review):
    path = os.environ.get(ENV)
    if not path:
        return
    with _lock:
        _windows[(path, window_id)] = bool(has_review)
        target = Path(path)
        try:
            # Same directory keeps replace atomic. Parent created it privately.
            with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as out:
                temporary = Path(out.name)
                json.dump({'review_open': any(value for (channel, _), value in _windows.items()
                                             if channel == path)}, out)
            os.replace(temporary, target)
        except OSError:
            if 'temporary' in locals():
                temporary.unlink(missing_ok=True)


def needs_confirmation(path):
    try:
        return json.loads(Path(path).read_text())['review_open'] is not False
    except (OSError, ValueError, KeyError, TypeError):
        return True  # An unreadable channel must not silently bypass the warning.


def register_close_guard(window, path):
    def closing():
        # Native backend shows its own dialog after this synchronous event.
        # Calling evaluate_js/a dialog here would deadlock Cocoa's main thread.
        window.confirm_close = needs_confirmation(path)
    window.events.closing += closing


CLOSE_MESSAGE = ('A transaction review may still be open. Cancel to return and use '
                 'Save review for later before quitting. Automatic recovery keeps '
                 'the last completed checkpoint; edits still being processed may '
                 'not be included. Quit anyway?')
