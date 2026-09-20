"""Run the real import page with fake Jev/vault and a disposable encrypted book.

Usage: python scripts/jev_browser_fixture.py --port 8629
The temporary book is removed on normal shutdown; no real key or ledger is read.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8629)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ledgertb-jev-browser-") as scratch:
        os.environ["ANTHROPIC_API_KEY"] = "fake-not-used"
        os.environ["LEDGERTB_DB_PATH"] = str(Path(scratch) / "synthetic.db")
        os.environ["LEDGERTB_BACKUP_DIR"] = str(Path(scratch) / "backups")
        from utils import secure_store
        vault = {"categorization_provider": "jev", "typesafe_api_key": "fake-not-used",
                 "anthropic_api_key": "fake-not-used", "openai_api_key": "fake-not-used"}
        secure_store.get_secret = lambda name: vault.get(name)
        secure_store.set_secret = lambda name, value: vault.__setitem__(name, value)
        secure_store.delete_secret = lambda name: vault.pop(name, None)
        from database import connection as db
        from database.crypto import derive_key
        if not db.ENCRYPTION_AVAILABLE:
            raise SystemExit("SQLCipher required")
        db.set_active_key(derive_key("disposable-synthetic-passphrase"))
        db.init_database()
        from models.client import Client
        from models.account import Account
        client_id = Client(name="Synthetic Jev Browser Co", entity_type="S-Corp").save(seed_accounts=False)
        cash = Account(client_id=client_id, account_number="1000", name="Checking", type="Asset", subtype="Cash")
        cash.save()
        office = Account(client_id=client_id, account_number="6100", name="Office Supplies", type="Expense")
        office.save()
        from services import jev_categorization as jev
        import streamlit as st
        def send(payload, key):
            st.session_state["fixture_calls"] = st.session_state.get("fixture_calls", 0) + 1
            if st.session_state.get("fixture_fail"):
                raise TimeoutError()
            answers = {}
            for key, question in payload["questions"].items():
                choice = f"account_{office.id}"
                answers[key] = {"type": "choice", "choice": choice, "confidence": .95,
                                "probabilities": {c: float(c == choice) for c in question["criteria"]}}
            return {"model": "fake-jev", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 10}}
        jev.send_request = send
        from services import review_categorization as ai
        def other_send(provider, payload, key):
            st.session_state["fixture_other_calls"] = st.session_state.get("fixture_other_calls", 0) + 1
            if st.session_state.get("fixture_fail"):
                raise TimeoutError()
            state = json.loads(payload['input'][0]['content'] if provider == 'openai' else payload['messages'][0]['content'])
            text = json.dumps({'suggestions': [dict(request_id=k, choice='insufficient_information',
                reason='Synthetic second opinion requests a receipt.') for k in state['transactions']]})
            if provider == 'openai':
                return dict(model=payload['model'], status='completed', output=[dict(type='message', content=[dict(type='output_text', text=text)])])
            return dict(model=payload['model'], stop_reason='end_turn', content=[dict(type='text', text=text)])
        ai.send_request = other_send
        import utils.client_selector as selector
        selector.render_client_selector = lambda: client_id
        st.page_link = lambda *args, **kwargs: None
        from streamlit.web import bootstrap
        options = {"server.address": "127.0.0.1", "server.port": args.port,
                   "server.headless": True, "browser.gatherUsageStats": False}
        bootstrap.load_config_options(options)
        bootstrap.run(str(ROOT / "tests/helpers/jev_browser_page.py"), False, [], options)


if __name__ == "__main__":
    main()
