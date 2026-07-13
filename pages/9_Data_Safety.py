import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import APP_VERSION, DATABASE_PATH
from database import init_database
from services.backups import backup_health, create_backup, list_backups, restore_backup
from services.production_readiness import get_readiness_checks, is_production_ready
from utils.client_selector import render_client_selector
from utils import icons

init_database()
st.set_page_config(page_title="Data Safety", page_icon=icons.SECURITY, layout="wide")
render_client_selector()

st.title("Data Safety")
st.caption(f"ProBooks {APP_VERSION} · Database: {DATABASE_PATH}")

if is_production_ready():
    st.success("Production safeguards are ready.")
else:
    st.error("TEST DATA ONLY — required production safeguards are incomplete.")

st.subheader("Production readiness")
for check in get_readiness_checks():
    status = "Pass" if check.passed else "Action required"
    st.markdown(f"**{check.label}** · {status}")
    st.caption(check.detail)

st.divider()
st.subheader("Verified backups")
st.warning("Backups are verified but remain plaintext until SQLCipher encryption is implemented.")
health = backup_health()
if health["latest"]:
    latest = health["latest"]
    st.write(f"Latest: {latest.created_at.astimezone():%Y-%m-%d %H:%M:%S %Z}")
    st.write(f"Size: {latest.size_bytes / 1024:,.1f} KB · SHA-256 verified")
else:
    st.warning(health["reason"])

if st.button("Create verified backup", type="primary"):
    try:
        record = create_backup()
        st.success(f"Backup created: {record.database_path.name}")
        st.rerun()
    except Exception as exc:
        st.error(f"Backup failed: {exc}")

backups = list_backups()
if backups:
    st.caption("Retention: 30 recent backups, plus 12 weekly and 7 monthly recovery points.")
    selected = st.selectbox(
        "Restore point",
        options=[r.database_path for r in backups],
        format_func=lambda p: p.name,
    )
    confirm = st.text_input(
        "Type RESTORE to replace the live database",
        placeholder="RESTORE",
    )
    if st.button("Restore selected backup", disabled=confirm != "RESTORE"):
        try:
            safety_copy = restore_backup(selected)
            st.success(f"Restore complete. Pre-restore safety copy: {safety_copy.name}")
            st.rerun()
        except Exception as exc:
            st.error(f"Restore failed: {exc}")
