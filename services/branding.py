"""Firm document branding: who prepared this, and what it should look like.

Firm-level, not per-client — the same practice brands every client's
deliverables. Stored in the database (single row) so the logo is encrypted at
rest and travels with backups.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from database.connection import get_cursor

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


def normalize_hex(value: str) -> str:
    """Return '#RRGGBB' or '' — anything unparseable falls back to default."""
    match = _HEX_RE.match((value or "").strip())
    return f"#{match.group(1).upper()}" if match else ""


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
    if logo is not None:
        if len(logo) > MAX_LOGO_BYTES:
            raise ValueError("Logo must be 2MB or smaller.")
        if logo_mime not in ALLOWED_LOGO_MIME:
            raise ValueError("Logo must be a PNG or JPEG.")
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
