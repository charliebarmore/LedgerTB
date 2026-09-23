"""LedgerTB brand header for the lock and gate screens (see branding/BRAND.md).

The rest of the app still wears the ProBooks-era navy; these are the first
surfaces brought onto the brand guide. Colors are the Ledger Labs palette
tokens, never picked fresh.
"""

import base64

import streamlit as st

from config import APP_NAME

# branding/logos/mark-transparent.svg, inlined: the frozen build ships assets/
# but not branding/, and the lock screen must never fail on a missing file.
_MARK_SVG = (
    '<svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="48" y="54" width="160" height="20" rx="3" fill="#E8913A"/>'
    '<rect x="118" y="54" width="20" height="118" rx="3" fill="#E8913A"/>'
    '<rect x="60" y="92" width="46" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="60" y="116" width="34" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="60" y="140" width="42" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="150" y="92" width="38" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="150" y="116" width="46" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="150" y="140" width="30" height="12" rx="3" fill="#FDFCEA"/>'
    '<rect x="60" y="186" width="136" height="6" rx="2" fill="#FDFCEA"/>'
    '<rect x="60" y="198" width="136" height="6" rx="2" fill="#FDFCEA"/>'
    '</svg>'
)
_MARK_URI = "data:image/svg+xml;base64," + base64.b64encode(_MARK_SVG.encode()).decode()

# The app doesn't bundle Playfair Display, so BRAND.md's Georgia fallback leads.
# Naming Playfair first is worse than useless on Linux: WebKitGTK takes
# fontconfig's substitute for a missing family (the default sans), so the
# later serif fallbacks never get a turn. Georgia maps to Liberation Serif there.
_WORDMARK_FONT = "Georgia, 'Liberation Serif', 'Noto Serif', serif"

TAGLINE = "Double-entry bookkeeping you can read, run, and change."

# Deep Teal field, Paper Cream text, Signal Orange "TB" (BRAND.md: in the
# wordmark "Ledger" is Paper Cream and "TB" is Signal Orange). Playfair Display
# for the wordmark, falling back to Georgia as the guide specifies for the app.
_HEADER_CSS = """
<style>
.ltb-hero { background: #1D434E; border-radius: 10px; padding: 1.6rem 1.9rem;
  display: flex; align-items: center; gap: 1.25rem; margin-bottom: 0.5rem; }
.ltb-hero img { width: 4.25rem; height: 4.25rem; flex: none; }
.ltb-hero .ltb-word { font-family: WORDMARK_FONT; font-weight: 700; font-size: 2.5rem; line-height: 1; color: #FDFCEA;
  letter-spacing: -0.01em; }
.ltb-hero .ltb-word span { color: #E8913A; }
.ltb-tag { color: #FDFCEA; opacity: 0.82; font-size: 0.98rem; margin-top: 0.45rem; }
.ltb-chip { display: inline-block; margin-top: 0.7rem; font-size: 0.78rem;
  color: #FDFCEA; border: 1px solid rgba(253, 252, 234, 0.3);
  border-radius: 999px; padding: 0.15rem 0.7rem; }
</style>
""".replace("WORDMARK_FONT", _WORDMARK_FONT)

# Ledger Green is the brand's call-to-action color. Injected only on the gate
# screens (they st.stop() before any app page renders), so the in-app navy
# buttons are untouched.
_GATE_BUTTON_CSS = """
<style>
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"] {
  background-color: #2D9148; border-color: #2D9148; color: #FFFFFF; }
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background-color: #257A3C; border-color: #257A3C; color: #FFFFFF; }
</style>
"""


def render_brand_header(*, chip: str = "") -> None:
    """The branded title block for lock, unlock, setup and refusal screens."""
    name = APP_NAME
    word = (f"{name[:-2]}<span>{name[-2:]}</span>"
            if name.endswith("TB") else name)
    chip_html = f'<div class="ltb-chip">{chip}</div>' if chip else ""
    st.html(
        _HEADER_CSS + _GATE_BUTTON_CSS
        + f'<div class="ltb-hero"><img src="{_MARK_URI}" alt="{name} mark">'
        + f'<div><div class="ltb-word">{word}</div>'
        + f'<div class="ltb-tag">{TAGLINE}</div>{chip_html}</div></div>'
    )
