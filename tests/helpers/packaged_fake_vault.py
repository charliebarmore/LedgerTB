"""Test-only keyring backend copied into a disposable bundle by the harness.

Never included by LedgerTB.spec. No OS vault calls, no persisted secrets.
The optional development key is read from its original file only for fictional
live acceptance; it is never copied into the bundle or browser transcript.
"""
import hashlib
import json
import os
from pathlib import Path
import urllib.error

from keyring.backend import KeyringBackend


class Keyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.values = {"categorization_provider": "jev"}
        directory = os.environ.get("LEDGERTB_FIXTURE_DIR")
        if not directory:
            # keyring.load_env catches KeyError and would try OS discovery.
            # Fail with a different exception so this fixture never falls back.
            raise RuntimeError("The fake vault requires a disposable fixture directory")
        scratch = Path(directory)
        assert (scratch / "synthetic-fixture.json").is_file()
        (scratch / "fake-vault-loaded").write_text("No operating-system vault access\n")
        key_file = os.environ.get("LEDGERTB_FIXTURE_KEY_FILE")
        if key_file:
            from dotenv import dotenv_values
            self.values["typesafe_api_key"] = dotenv_values(key_file).get("TYPESAFE_API_KEY", "")
        else:
            self.values["typesafe_api_key"] = "fictional-test-key"
        from services import jev_categorization as jev
        original = jev.send_request

        def tracked(payload, key):
            event = {"questions": len(payload["questions"]),
                     "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                     "live": bool(key_file), "network_attempted": False}
            try:
                if (scratch / "offline").exists():
                    raise urllib.error.URLError("Synthetic offline fixture")
                if key_file:
                    event["network_attempted"] = True
                    result = original(payload, key)
                else:
                    answers = {}
                    for qid, question in payload["questions"].items():
                        choice = next((k for k, v in question["criteria"].items()
                                       if "6100:" in v), "insufficient_information")
                        answers[qid] = {"type": "choice", "choice": choice, "confidence": .95,
                                        "probabilities": {c: float(c == choice) for c in question["criteria"]}}
                    result = {"model": "synthetic-fixture", "answers": answers, "usage": {}}
                event.update(model=result.get("model"), usage=result.get("usage"), success=True)
                return result
            except Exception as exc:
                event.update(error_type=type(exc).__name__, success=False)
                raise
            finally:
                with (scratch / "requests.jsonl").open("a") as log:
                    log.write(json.dumps(event) + "\n")

        jev.send_request = tracked

    def get_password(self, service, username):
        return self.values.get(username) if service == "com.ledgerlabs.ledgertb" else None

    def set_password(self, service, username, password):
        if service == "com.ledgerlabs.ledgertb":
            self.values[username] = password

    def delete_password(self, service, username):
        if service == "com.ledgerlabs.ledgertb":
            self.values.pop(username, None)
