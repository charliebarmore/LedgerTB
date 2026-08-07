import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from services.branding import (
    ALLOWED_LOGO_MIME,
    MAX_LOGO_BYTES,
    get_branding,
    normalize_hex,
    save_branding,
)
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons

st.set_page_config(page_title="Firm Settings", page_icon=icons.FIRM, layout="wide")

require_unlock()
init_database()

render_client_selector()

st.title("Firm Settings")

st.subheader("Document branding")
st.caption(
    "Your firm's identity on generated deliverables — the close package PDF "
    "masthead, headings, and footer, and the Excel summary. Firm-level: the "
    "same brand applies to every client's reports. The logo is stored inside "
    "the encrypted database and travels with backups."
)

branding = get_branding()

name_col, tagline_col = st.columns(2)
with name_col:
    # Placeholders are examples every user sees, so keep them generic -- a real
    # firm name here reads as the app's owner, not as a prompt to type your own.
    firm_name = st.text_input("Firm name", value=branding.firm_name,
                              placeholder="Your firm name")
with tagline_col:
    tagline = st.text_input("Tagline / address line", value=branding.tagline,
                            placeholder="City, ST · yourfirm.com")

accent_col, logo_col = st.columns(2)
with accent_col:
    accent = st.color_picker("Accent color (headings and rules)",
                             value=branding.accent_hex or "#14141A")
    st.caption("Pick your brand color; black keeps the default look.")
with logo_col:
    uploaded_logo = st.file_uploader(
        "Logo (PNG or JPEG, 2MB max)", type=["png", "jpg", "jpeg"],
        key="brand_logo_upload",
    )
    if branding.logo and not uploaded_logo:
        st.image(branding.logo, caption="Current logo", width=160)

remove_logo = False
if branding.logo:
    remove_logo = st.checkbox("Remove the saved logo", value=False)

if st.button("Save branding", type="primary"):
    logo_bytes = None
    logo_mime = None
    if uploaded_logo is not None:
        logo_bytes = uploaded_logo.getvalue()
        logo_mime = uploaded_logo.type
        if logo_mime not in ALLOWED_LOGO_MIME:
            st.error("The logo must be a PNG or JPEG.")
            st.stop()
        if len(logo_bytes) > MAX_LOGO_BYTES:
            st.error("The logo must be 2MB or smaller.")
            st.stop()
    try:
        saved = save_branding(
            firm_name=firm_name,
            tagline=tagline,
            accent_hex=normalize_hex(accent),
            logo=logo_bytes,
            logo_mime=logo_mime,
            keep_existing_logo=not remove_logo,
        )
        st.success("Branding saved. The next report you export will carry it.")
        if remove_logo and not uploaded_logo:
            st.rerun()
    except ValueError as exc:
        st.error(str(exc))

st.divider()
st.subheader("AI categorization")
st.caption(
    "Powered by your own Anthropic API key, stored in the system credential "
    "vault — never in a file. When suggestions run, transaction dates, "
    "descriptions, amounts, and your account names/numbers are sent to "
    "Anthropic's API. Suggestions only; nothing posts without review."
)

from config import ANTHROPIC_API_KEY
from utils.secure_store import delete_secret, get_secret, set_secret

_saved_key = get_secret("anthropic_api_key")
if ANTHROPIC_API_KEY:
    st.success("AI categorization is enabled for this session.")
elif _saved_key:
    st.info("An API key is saved. Restart ProBooks to enable AI categorization.")
else:
    st.warning("Not configured — add an Anthropic API key below.")

api_key_input = st.text_input(
    "Anthropic API Key",
    type="password",
    placeholder="sk-ant-...",
    help="Get your API key at https://console.anthropic.com/",
    key="firm_settings_api_key",
)
key_cols = st.columns([1, 1, 3])
with key_cols[0]:
    if st.button("Save key", type="primary", disabled=not api_key_input):
        try:
            set_secret("anthropic_api_key", api_key_input.strip())
            st.success("Saved to the system credential vault. Restart ProBooks to enable.")
        except Exception as exc:
            st.error(f"Could not save the API key securely: {exc}")
with key_cols[1]:
    if _saved_key and st.button("Remove key"):
        delete_secret("anthropic_api_key")
        st.success("API key removed from the credential vault.")
        st.rerun()

st.divider()
st.caption(
    "Coming later: custom report fonts (PDFs need embedded font files, not "
    "webfont links) and per-report templates."
)
