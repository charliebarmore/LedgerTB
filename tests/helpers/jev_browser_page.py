"""Only launched by scripts/jev_browser_fixture.py; never a production entry point."""
from pathlib import Path
import runpy
import streamlit as st

from database import connection as db
from models.account import Account
from utils.import_review import ensure_row_ids

if "ledgertb-jev-browser-" not in str(db.DATABASE_PATH):
    st.error("Use the disposable Jev browser fixture launcher.")
    st.stop()
accounts = Account.get_all(1)
cash_id = next(a.id for a in accounts if a.type == "Asset")
if "fixture_ready" not in st.session_state:
    st.session_state.fixture_ready = True
    st.session_state.import_active_tab = "Review & Categorize"
    st.session_state.transactions_to_review = ensure_row_ids([
        {"date": "2026-01-01", "description": "Cedar Paper: printer paper receipt", "amount": -33.33,
         "bank_account_id": cash_id, "include": False},
    ])
from utils import secure_store

def _change_fixture_provider():
    secure_store.set_secret("categorization_provider", "off" if st.session_state.fixture_jev_off else "jev")

st.checkbox("Disable Jev provider", key="fixture_jev_off", on_change=_change_fixture_provider)
st.checkbox("Simulate network timeout", key="fixture_fail")
st.caption(f"Synthetic failure mode: {'on' if st.session_state.fixture_fail else 'off'}")
if st.button("Change synthetic evidence"):
    st.session_state.transactions_to_review[0]["description"] += " changed"
    st.session_state.fixture_revision = st.session_state.get("fixture_revision", 0) + 1
st.caption(f"Fixture evidence revision: {st.session_state.get('fixture_revision', 0)}")
runpy.run_path(str(Path(__file__).resolve().parents[2] / "pages/4_Import_Transactions.py"))
st.divider()
st.caption(f"Synthetic transport calls: {st.session_state.get('fixture_calls', 0)}")
