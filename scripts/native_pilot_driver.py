"""Local test control for the actual Cocoa/WebKit source desktop window.

Requires a disposable packaged_jev_fixture under output/. Never ships in the app.
Commands/results stay in the chosen output directory; no listening control port.
All credentials/providers use the fake-vault fixture. Native dialogs are real.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--startup-timeout', type=float, default=60,
                        help='Diagnostic allowance; longer values do not establish startup performance')
    args = parser.parse_args()
    fixture, output = args.data_dir.resolve(), args.output.resolve()
    if not all(p.is_relative_to(ROOT / 'output') for p in (fixture, output)):
        parser.error('Use disposable directories under this checkout’s output/')
    if json.loads((fixture / 'synthetic-fixture.json').read_text()).get('synthetic') is not True:
        parser.error('Create a fictional fixture first')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'starting.json').write_text(json.dumps({'pid': os.getpid(), 'startup_allowance': args.startup_timeout}))
    for prefix in ('LEDGERTB', 'PROBOOKS'):
        for suffix in ('MODE', 'PORT', 'UI_TOKEN', 'PARENT_PID', 'DB_PATH', 'BACKUP_DIR'):
            os.environ.pop(f'{prefix}_{suffix}', None)
    os.environ.pop('LEDGERTB_FIXTURE_KEY_FILE', None)
    os.environ.update(LEDGERTB_DATA_DIR=str(fixture), LEDGERTB_FIXTURE_DIR=str(fixture),
        PYTHONPATH=os.pathsep.join((str(ROOT / 'tests/helpers'), str(ROOT))),
        PYTHON_KEYRING_BACKEND='packaged_fake_vault.Keyring', PYTHON_DOTENV_DISABLED='1',
        ANTHROPIC_API_KEY='fake-not-used', OPENAI_API_KEY='fake-not-used', TYPESAFE_API_KEY='fake-not-used',
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests/helpers')]
    import webview
    import run_ledgertb
    from AppKit import NSApplication, NSButton, NSSavePanel
    from Foundation import NSURL
    from PyObjCTools.AppHelper import callAfter

    original = webview.create_window
    wait_ready = run_ledgertb._wait_until_ready
    run_ledgertb._wait_until_ready = lambda url: wait_ready(url, timeout=args.startup_timeout)
    closed = threading.Event()
    started = threading.Event()

    def on_main(action):
        done = threading.Event()
        result = {}
        def invoke():
            try:
                result['value'] = action()
            except Exception as exc:
                result['error'] = repr(exc)
            finally:
                done.set()
        callAfter(invoke)
        if not done.wait(20):
            raise TimeoutError('Cocoa control did not return')
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result.get('value')

    def views(view):
        yield view
        for child in view.subviews():
            yield from views(child)

    def native_status():
        app = NSApplication.sharedApplication()
        modal = app.modalWindow()
        return [dict(title=str(w.title()), kind=type(w).__name__, modal=w == modal,
                     buttons=[str(v.title()) for v in views(w.contentView()) if isinstance(v, NSButton)],
                     text=[str(v.stringValue()) for v in views(w.contentView()) if hasattr(v, 'stringValue')])
                for w in app.windows() if w.isVisible()]

    def native_choice(command):
        modal = NSApplication.sharedApplication().modalWindow()
        if modal is None:
            raise ValueError('No native modal dialog is open')
        if 'filename' in command:
            if not isinstance(modal, NSSavePanel):
                raise ValueError('Expected a native save panel')
            destination = output / 'downloads'
            destination.mkdir(exist_ok=True)
            name = command['filename']
            if Path(name).name != name:
                raise ValueError('Use a filename, not a path')
            modal.setDirectoryURL_(NSURL.fileURLWithPath_(str(destination)))
            modal.setNameFieldStringValue_(name)
            if Path(str(modal.directoryURL().path())).resolve() != destination.resolve():
                raise ValueError('Native save destination has not reached the disposable directory')
        buttons = [v for v in views(modal.contentView()) if isinstance(v, NSButton)
                   and str(v.title()) == command['button'] and v.isEnabled()]
        if len(buttons) != 1:
            raise ValueError('Expected exactly one enabled native dialog button')
        buttons[0].performClick_(None)
        return True

    def serve(window):
        (output / 'ready.json').write_text(json.dumps({'pid': os.getpid(), 'url': window.get_current_url()}))
        seen = set()
        while not closed.wait(.2):
            for command_path in sorted(output.glob('command-*.json')):
                if command_path.name in seen:
                    continue
                seen.add(command_path.name)
                try:
                    command = json.loads(command_path.read_text())
                    op = command['op']
                    if op == 'eval':
                        value = window.evaluate_js(command['script'])
                    elif op == 'native_status':
                        value = on_main(native_status)
                    elif op == 'native_choice':
                        value = on_main(lambda: native_choice(command))
                    elif op == 'close':
                        # Async: the dialog must not block this command reader.
                        callAfter(lambda: window.native.performClose_(None))
                        value = 'Native window close requested'
                    elif op == 'quit':
                        callAfter(lambda: NSApplication.sharedApplication().terminate_(None))
                        value = 'Native application quit requested'
                    else:
                        raise ValueError('Unknown test command')
                    result = {'ok': True, 'value': value}
                except Exception as exc:
                    result = {'ok': False, 'error': repr(exc)}
                (output / command_path.name.replace('command-', 'result-')).write_text(json.dumps(result, default=str))

    def create_window(*a, **kw):
        window = original(*a, **kw)
        window.events.closed += closed.set
        def loaded():
            if not started.is_set():
                started.set()
                threading.Thread(target=serve, args=(window,), daemon=True).start()
        window.events.loaded += loaded
        return window

    webview.create_window = create_window
    return run_ledgertb.main()


if __name__ == '__main__':
    raise SystemExit(main())
