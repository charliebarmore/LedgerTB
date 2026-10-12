"""Test-only vault for a disposable frozen review-check bundle, never shipped."""
import os
from pathlib import Path

from keyring.backend import KeyringBackend


class Keyring(KeyringBackend):
    priority = 1

    def __init__(self):
        directory = os.environ.get("LEDGERTB_FIXTURE_DIR")
        # keyring catches KeyError and falls back to OS discovery. Fail with a
        # different exception so a missing fixture can never touch the OS vault.
        if not directory:
            raise RuntimeError("The review vault requires a disposable directory")
        root = Path(directory)
        if not (root / "fixture.json").is_file():
            raise RuntimeError("The review vault requires the disposable fixture")
        self.values = {}
        (root / "fake-vault-loaded").write_text("No operating-system vault access\n")

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        self.values.pop((service, username), None)
