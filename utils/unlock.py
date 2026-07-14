"""Passphrase gate for the encrypted database.

ProBooks encrypts its SQLite database with SQLCipher. The key is derived from a
passphrase entered at launch and held only in this process -- never written to
disk or the OS keychain (see database/connection). Every page calls
require_unlock() right after st.set_page_config; until the passphrase is set the
page renders only the gate and stops.

The gate is satisfied by the *process* holding an active key, not by session
state, so one unlock covers the whole launch (and the pytest ``db`` fixture,
which sets a key directly, transparently passes the gate).
"""

import streamlit as st

from config import DATABASE_PATH, APP_NAME
from database import connection as dbconn
from database.crypto import (
    database_state,
    derive_key,
    encrypt_plaintext_db,
    verify_passphrase,
)

MIN_PASSPHRASE_LEN = 8


def require_unlock():
    """Ensure the database is unlocked, or render the gate and stop the page."""
    if dbconn.has_active_key():
        return
    _render_gate(database_state(DATABASE_PATH))
    st.stop()


def _valid_new_passphrase(passphrase: str, confirm: str) -> bool:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        st.error(f"Use at least {MIN_PASSPHRASE_LEN} characters.")
        return False
    if passphrase != confirm:
        st.error("Passphrases do not match.")
        return False
    return True


def _render_gate(state: str):
    # Keep the lock screen clean: no client nav, just the passphrase prompt.
    st.markdown(f"## 🔒 {APP_NAME}")
    if state == "encrypted":
        _unlock_form()
    elif state == "plaintext":
        _migrate_form()
    else:  # "absent"
        _setup_form()


def _unlock_form():
    st.caption("Enter your passphrase to unlock the database.")
    with st.form("db_unlock"):
        passphrase = st.text_input("Passphrase", type="password")
        if st.form_submit_button("Unlock", type="primary"):
            if verify_passphrase(DATABASE_PATH, passphrase):
                dbconn.set_active_key(derive_key(passphrase))
                st.rerun()
            else:
                st.error("Incorrect passphrase.")


def _setup_form():
    st.caption(
        "First run — create a passphrase to encrypt this database. It is not "
        "stored anywhere, so if you lose it the data cannot be recovered. A "
        "longer passphrase (a memorable sentence) is stronger than a short one."
    )
    with st.form("db_setup"):
        passphrase = st.text_input("New passphrase", type="password")
        confirm = st.text_input("Confirm passphrase", type="password")
        if st.form_submit_button("Create encrypted database", type="primary"):
            if _valid_new_passphrase(passphrase, confirm):
                # First keyed connection (init_database, next run) creates the
                # database already encrypted under this passphrase.
                dbconn.set_active_key(derive_key(passphrase))
                st.rerun()


def _migrate_form():
    st.warning(
        "An existing **unencrypted** database was found. Set a passphrase to "
        "encrypt it. A one-time copy of the unencrypted file is kept next to it "
        "(delete that copy once you've confirmed the encrypted database works)."
    )
    with st.form("db_migrate"):
        passphrase = st.text_input("New passphrase", type="password")
        confirm = st.text_input("Confirm passphrase", type="password")
        if st.form_submit_button("Encrypt existing database", type="primary"):
            if _valid_new_passphrase(passphrase, confirm):
                backup = encrypt_plaintext_db(DATABASE_PATH, passphrase)
                dbconn.set_active_key(derive_key(passphrase))
                st.session_state["_migration_backup_name"] = backup.name
                st.rerun()
