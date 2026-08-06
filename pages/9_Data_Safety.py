import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import APP_VERSION
from database import init_database
from database import connection as dbconn
from models.audit_log import AuditLog
from models.client import Client
from services.backups import backup_health, create_backup, list_backups, restore_backup
from services.production_readiness import get_readiness_checks, is_production_ready
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import books, icons

st.set_page_config(page_title="Data Safety", page_icon=icons.SECURITY, layout="wide")
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

client_id = render_client_selector()


def audit_safety_event(action, event_name, details):
    """Record a filesystem operation against a visible client audit stream."""
    audit_client = Client.get_by_id(client_id) if client_id else None
    if not audit_client:
        audit_client = Client.get_first()
    if not audit_client:
        st.warning("Operation succeeded, but no client exists to receive its audit event.")
        return
    try:
        AuditLog.log_event(audit_client.id, action, event_name, details)
    except Exception as exc:
        st.warning(f"Operation succeeded, but its audit event could not be recorded: {exc}")

st.title("Data Safety")
st.caption(f"ProBooks {APP_VERSION} · Book: {dbconn.DATABASE_PATH}")

# ---- Book file (firm mode) -------------------------------------------------
from utils import book_lock as _bl

_book_cols = st.columns([3, 1])
with _book_cols[0]:
    _holder = _bl.read_lock(dbconn.DATABASE_PATH)
    if dbconn.READ_ONLY:
        st.caption("Open **read-only**"
                   + (f" — in use by {_bl.describe(_holder)}" if _holder else ""))
    elif _holder:
        st.caption(f"In use by **{_bl.describe(_holder)}** (that's this session)")
with _book_cols[1]:
    if st.button("Switch book…", help="Close this book and choose another "
                 "(shared-drive books included)"):
        _bl.release(dbconn.DATABASE_PATH)
        dbconn.READ_ONLY = False
        dbconn.clear_active_key()
        st.rerun()

from utils import unlock as _unlock
from utils.secure_store import get_secret as _gs

if _gs(_unlock.saved_key_name(dbconn.DATABASE_PATH)):
    _rem_cols = st.columns([3, 1])
    with _rem_cols[0]:
        st.caption("This book's passphrase is **remembered on this machine** "
                   "(system credential vault) — the app opens it without asking.")
    with _rem_cols[1]:
        if st.button("Forget passphrase"):
            _unlock.forget_saved_key(dbconn.DATABASE_PATH)
            audit_safety_event("EXPORT", "book_key_forgotten", {})
            st.rerun()

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
if dbconn.ENCRYPTION_AVAILABLE:
    st.caption("Backups are written encrypted under the same passphrase as the database.")
else:
    st.warning("Encryption is off (SQLCipher not installed), so backups are plaintext like the database.")
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
        audit_safety_event("BACKUP", "database_backup", {
            "reason": "manual", "backup_file": record.database_path.name,
            "sha256": record.sha256, "size_bytes": record.size_bytes,
            "integrity_verified": record.integrity_ok,
        })
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
            # The restored database may contain a different client set, so the
            # helper resolves the audit client again after replacement.
            audit_safety_event("RESTORE", "database_restore", {
                "restored_from": selected.name,
                "pre_restore_backup": safety_copy.name,
                "integrity_verified": True,
            })
            st.success(f"Restore complete. Pre-restore safety copy: {safety_copy.name}")
            st.rerun()
        except Exception as exc:
            st.error(f"Restore failed: {exc}")

st.divider()
st.subheader("Assistant access (MCP)")
st.caption(
    "Lets an AI assistant on THIS computer (Claude Desktop, Claude Code) use "
    "these books through a local, stdio-only MCP server. You choose whether it "
    "can only read, can also file proposals for your review, or can post new "
    "balanced entries. The database engine blocks anything above that level "
    "and always blocks edits and deletes. Enabling stores the derived database "
    "key (never your passphrase) in the system credential vault; disabling "
    "revokes the next tool call. Tool results may be sent by your MCP client to "
    "its configured AI provider, so use only a provider your firm has approved."
)

import json

from utils.secure_store import delete_secret as _mcp_delete
from utils.secure_store import get_secret as _mcp_get
from utils.secure_store import set_secret as _mcp_set

MCP_KEY_SECRET = "mcp_db_key"
MCP_LEVEL_SECRET = "mcp_access_level"
_MCP_LEVELS = {
    "read": "Read only — query the books, change nothing",
    "propose": "Read + propose — file drafts and stage imports; you post everything (recommended)",
    "post": "Read + propose + post — may also post balanced entries, append-only",
}
_mcp_enabled = bool(_mcp_get(MCP_KEY_SECRET))
_mcp_level = _mcp_get(MCP_LEVEL_SECRET) or "propose"
if _mcp_level not in _MCP_LEVELS:
    _mcp_level = "propose"
_mcp_book_is_local = books.is_local_book(dbconn.DATABASE_PATH)

if _mcp_enabled:
    st.success(f"Assistant access is enabled — level: "
               f"**{_mcp_level}** ({_MCP_LEVELS[_mcp_level].split(' — ')[1]}).")
else:
    st.info("Assistant access is off. Assistants cannot read these books.")

_picked_level = st.radio(
    "Access level",
    options=list(_MCP_LEVELS),
    format_func=lambda lv: _MCP_LEVELS[lv],
    index=list(_MCP_LEVELS).index(_mcp_level),
    key="mcp_level_pick",
)
_book_level_ok = _mcp_book_is_local or _picked_level == "read"
if not _mcp_book_is_local:
    st.warning(
        "This book uses a custom or shared-drive path. Assistant access is "
        "limited to read only because the MCP process does not participate in "
        "the book's one-writer lock. Move the book to ProBooks' local data "
        "folder to enable proposals or direct posting."
    )
_post_ok = True
if _picked_level == "post":
    st.warning(
        "At this level the assistant can post journal entries on its own — "
        "**append-only**: even here it can never edit or delete anything, and "
        "every entry it posts is audited and marked \"Posted by assistant "
        "(MCP)\". Corrections are new, visible entries.", icon="✒️",
    )
    _post_ok = st.checkbox(
        "I understand the assistant will be able to post entries",
        key="mcp_post_consent",
    )

mcp_cols = st.columns([1, 1, 3])
with mcp_cols[0]:
    if not _mcp_enabled and st.button("Enable assistant access", type="primary",
                                      disabled=not _post_ok or not _book_level_ok):
        session_key = dbconn.get_active_key()
        if not session_key:
            st.error("Unlock the database first.")
        else:
            try:
                # Store the permission first and the enabling key last. A
                # partial credential-vault write must never enable access with
                # an unintended fallback level.
                _mcp_set(MCP_LEVEL_SECRET, _picked_level)
                _mcp_set(MCP_KEY_SECRET, session_key)
                audit_safety_event("EXPORT", "mcp_access_enabled",
                                   {"level": _picked_level})
                st.rerun()
            except Exception as exc:
                _mcp_delete(MCP_KEY_SECRET)
                _mcp_delete(MCP_LEVEL_SECRET)
                st.error(f"Could not store the key securely: {exc}")
    if (_mcp_enabled and _picked_level != _mcp_level
            and st.button("Change level", type="primary",
                          disabled=not _post_ok or not _book_level_ok)):
        try:
            _mcp_set(MCP_LEVEL_SECRET, _picked_level)
            audit_safety_event("EXPORT", "mcp_access_level_changed",
                               {"from": _mcp_level, "to": _picked_level})
            st.rerun()
        except Exception as exc:
            st.error(f"Could not change assistant access securely: {exc}")
with mcp_cols[1]:
    if _mcp_enabled and st.button("Disable assistant access"):
        _mcp_delete(MCP_KEY_SECRET)
        _mcp_delete(MCP_LEVEL_SECRET)
        audit_safety_event("EXPORT", "mcp_access_disabled", {})
        st.rerun()
if _mcp_enabled and _picked_level != _mcp_level:
    st.caption("Level changes apply on the assistant's next tool call.")

if _mcp_enabled:
    if getattr(sys, "frozen", False):
        _mcp_config = {"command": sys.executable, "args": [],
                       "env": {"PROBOOKS_MODE": "mcp"}}
    else:
        _mcp_config = {"command": sys.executable,
                       "args": [str(Path(__file__).resolve().parent.parent / "mcp_server.py")]}
    st.caption(
        "Add this to your MCP client's configuration (Claude Desktop: "
        "Settings → Developer → Edit Config, inside `mcpServers`):"
    )
    st.code(json.dumps({"probooks": _mcp_config}, indent=2), language="json")

st.divider()
st.caption(
    "AI categorization setup (your Anthropic API key) lives on the Firm "
    "Settings page with the rest of the firm-level configuration."
)
st.page_link("pages/12_Firm_Settings.py", label="Firm Settings", icon=icons.FIRM)
