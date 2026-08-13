"""Firm and client document identities for co-branded deliverables.

Both layers live inside the encrypted book and travel with its backups. Client
branding identifies whose books the package contains; firm branding identifies
the professional who prepared it.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from database.connection import get_cursor
from models.audit_log import AuditLog
from utils.actor import current_actor

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_LOGO_MIME = {"image/png", "image/jpeg"}


@dataclass
class FirmBranding:
    firm_name: str = ""
    tagline: str = ""
    accent_hex: str = ""          # normalized "#RRGGBB" or "" for default
    logo: Optional[bytes] = None
    logo_mime: Optional[str] = None

    @property
    def is_branded(self) -> bool:
        return bool(self.firm_name or self.accent_hex or self.logo)


@dataclass
class ClientBranding:
    client_id: int = 0
    display_name: str = ""
    tagline: str = ""
    accent_hex: str = ""
    logo: Optional[bytes] = None
    logo_mime: Optional[str] = None

    @property
    def is_branded(self) -> bool:
        return bool(self.display_name or self.tagline or self.accent_hex or self.logo)


def normalize_hex(value: str) -> str:
    """Return '#RRGGBB' or '' — anything unparseable falls back to default."""
    match = _HEX_RE.match((value or "").strip())
    return f"#{match.group(1).upper()}" if match else ""


def _validated_accent(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = value.strip()
    normalized = normalize_hex(raw)
    if raw and not normalized:
        raise ValueError("Accent color must be a six-digit hex color.")
    return normalized


def _validate_logo(logo: Optional[bytes], logo_mime: Optional[str]) -> None:
    if logo is None:
        return
    if len(logo) > MAX_LOGO_BYTES:
        raise ValueError("Logo must be 2MB or smaller.")
    if logo_mime not in ALLOWED_LOGO_MIME:
        raise ValueError("Logo must be a PNG or JPEG.")


def get_branding() -> FirmBranding:
    with get_cursor() as cursor:
        cursor.execute("SELECT * FROM firm_branding WHERE id = 1")
        row = cursor.fetchone()
    if not row:
        return FirmBranding()
    return FirmBranding(
        firm_name=row["firm_name"] or "",
        tagline=row["tagline"] or "",
        accent_hex=normalize_hex(row["accent_hex"]),
        logo=row["logo"],
        logo_mime=row["logo_mime"],
    )


def save_branding(
    firm_name: str,
    tagline: str = "",
    accent_hex: str = "",
    logo: Optional[bytes] = None,
    logo_mime: Optional[str] = None,
    keep_existing_logo: bool = True,
) -> FirmBranding:
    """Persist branding. Without a new logo, the stored one is kept unless
    keep_existing_logo is False (explicit removal)."""
    _validate_logo(logo, logo_mime)
    accent = normalize_hex(accent_hex)

    with get_cursor(commit=True) as cursor:
        cursor.execute("SELECT logo, logo_mime FROM firm_branding WHERE id = 1")
        existing = cursor.fetchone()
        if logo is None and keep_existing_logo and existing:
            logo, logo_mime = existing["logo"], existing["logo_mime"]
        cursor.execute(
            """
            INSERT INTO firm_branding (id, firm_name, tagline, accent_hex,
                                       logo, logo_mime, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                firm_name = excluded.firm_name,
                tagline = excluded.tagline,
                accent_hex = excluded.accent_hex,
                logo = excluded.logo,
                logo_mime = excluded.logo_mime,
                updated_at = excluded.updated_at
            """,
            (firm_name.strip(), tagline.strip(), accent,
             logo, logo_mime, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
    return get_branding()


def _client_branding_from_row(client_id: int, row) -> ClientBranding:
    if not row:
        return ClientBranding(client_id=client_id)
    return ClientBranding(
        client_id=client_id,
        display_name=row["display_name"] or "",
        tagline=row["tagline"] or "",
        accent_hex=normalize_hex(row["accent_hex"]),
        logo=row["logo"],
        logo_mime=row["logo_mime"],
    )


def get_client_branding(client_id: int) -> ClientBranding:
    with get_cursor() as cursor:
        row = cursor.execute(
            "SELECT * FROM client_branding WHERE client_id = ?", (client_id,)
        ).fetchone()
    return _client_branding_from_row(client_id, row)


def _save_client_branding_with_cursor(
    cursor,
    client_id: int,
    display_name: str,
    tagline: str,
    accent_hex: str,
    logo: Optional[bytes],
    logo_mime: Optional[str],
    keep_existing_logo: bool,
) -> None:
    if not cursor.execute("SELECT 1 FROM clients WHERE id = ?", (client_id,)).fetchone():
        raise ValueError("Client not found.")
    existing = cursor.execute(
        "SELECT * FROM client_branding WHERE client_id = ?", (client_id,)
    ).fetchone()
    if logo is None and keep_existing_logo and existing:
        logo, logo_mime = existing["logo"], existing["logo_mime"]
    actor = current_actor()
    cursor.execute(
        "INSERT INTO client_branding "
        "(client_id, display_name, tagline, accent_hex, logo, logo_mime, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(client_id) DO UPDATE SET "
        "display_name = excluded.display_name, tagline = excluded.tagline, "
        "accent_hex = excluded.accent_hex, logo = excluded.logo, "
        "logo_mime = excluded.logo_mime, updated_by = excluded.updated_by, "
        "updated_at = CURRENT_TIMESTAMP",
        (client_id, display_name.strip(), tagline.strip(), accent_hex,
         logo, logo_mime, actor),
    )
    AuditLog.write(
        cursor, client_id, "client_branding", client_id,
        "UPDATE" if existing else "INSERT",
        old_values=(
            {"display_name": existing["display_name"],
             "tagline": existing["tagline"],
             "accent_hex": existing["accent_hex"],
             "logo_present": bool(existing["logo"])}
            if existing else None
        ),
        new_values={"display_name": display_name.strip(),
                    "tagline": tagline.strip(), "accent_hex": accent_hex,
                    "logo_present": bool(logo)},
    )


def save_client_branding(
    client_id: int,
    display_name: str = "",
    tagline: str = "",
    accent_hex: str = "",
    logo: Optional[bytes] = None,
    logo_mime: Optional[str] = None,
    keep_existing_logo: bool = True,
) -> ClientBranding:
    """Save the human-approved client identity used on deliverables."""
    _validate_logo(logo, logo_mime)
    accent = _validated_accent(accent_hex) or ""
    with get_cursor(commit=True) as cursor:
        _save_client_branding_with_cursor(
            cursor, client_id, display_name, tagline, accent, logo, logo_mime,
            keep_existing_logo,
        )
    return get_client_branding(client_id)


def propose_client_branding(
    client_id: int,
    display_name: Optional[str] = None,
    tagline: Optional[str] = None,
    accent_hex: Optional[str] = None,
    rationale: str = "",
) -> int:
    """File text/color branding for human approval; logos are never read by MCP."""
    if display_name is None and tagline is None and accent_hex is None:
        raise ValueError("Propose at least one client branding field.")
    accent = _validated_accent(accent_hex)
    with get_cursor(commit=True) as cursor:
        if not cursor.execute("SELECT 1 FROM clients WHERE id = ?", (client_id,)).fetchone():
            raise ValueError("Client not found.")
        cursor.execute(
            "INSERT INTO client_branding_proposals "
            "(client_id, display_name, tagline, accent_hex, rationale, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id,
             display_name.strip() if display_name is not None else None,
             tagline.strip() if tagline is not None else None,
             accent, (rationale or "").strip(), current_actor()),
        )
        proposal_id = cursor.lastrowid
        AuditLog.write(
            cursor, client_id, "client_branding_proposals", proposal_id, "INSERT",
            new_values={"display_name": display_name, "tagline": tagline,
                        "accent_hex": accent},
        )
    return proposal_id


def pending_client_branding_proposals(client_id: int) -> list[dict]:
    with get_cursor() as cursor:
        return [dict(row) for row in cursor.execute(
            "SELECT * FROM client_branding_proposals WHERE client_id = ? "
            "AND status = 'pending' ORDER BY id", (client_id,),
        ).fetchall()]


def pending_client_branding_count(client_id: int) -> int:
    with get_cursor() as cursor:
        return int(cursor.execute(
            "SELECT COUNT(*) n FROM client_branding_proposals "
            "WHERE client_id = ? AND status = 'pending'", (client_id,),
        ).fetchone()["n"])


def resolve_client_branding_proposal(
    client_id: int, proposal_id: int, accept: bool
) -> ClientBranding:
    actor = current_actor()
    if actor.endswith("(AI)"):
        raise PermissionError("An assistant cannot approve client branding.")
    with get_cursor(commit=True) as cursor:
        proposal = cursor.execute(
            "SELECT * FROM client_branding_proposals WHERE id = ? "
            "AND client_id = ? AND status = 'pending'", (proposal_id, client_id),
        ).fetchone()
        if not proposal:
            raise ValueError("Pending client branding proposal not found.")
        if accept:
            existing = cursor.execute(
                "SELECT * FROM client_branding WHERE client_id = ?", (client_id,)
            ).fetchone()
            current = _client_branding_from_row(client_id, existing)
            _save_client_branding_with_cursor(
                cursor, client_id,
                proposal["display_name"] if proposal["display_name"] is not None
                else current.display_name,
                proposal["tagline"] if proposal["tagline"] is not None
                else current.tagline,
                proposal["accent_hex"] if proposal["accent_hex"] is not None
                else current.accent_hex,
                None, None, True,
            )
        status = "accepted" if accept else "dismissed"
        cursor.execute(
            "UPDATE client_branding_proposals SET status = ?, resolved_by = ?, "
            "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, actor, proposal_id),
        )
        AuditLog.write(
            cursor, client_id, "client_branding_proposals", proposal_id, "UPDATE",
            old_values={"status": "pending"}, new_values={"status": status},
        )
    return get_client_branding(client_id)
