import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from models.client import Client
from services.branding import (
    ALLOWED_LOGO_MIME,
    MAX_LOGO_BYTES,
    get_branding,
    get_client_branding,
    normalize_hex,
    pending_client_branding_proposals,
    resolve_client_branding_proposal,
    save_branding,
    save_client_branding,
)
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons

st.set_page_config(page_title="Firm Settings", page_icon=icons.FIRM, layout="wide")

require_unlock()
init_database()

client_id = render_client_selector()

st.title("Firm Settings")

st.subheader("Document branding")
st.caption(
    "Your firm's identity on generated deliverables — the close package PDF "
    "masthead, headings, and footer, and the Excel summary. Firm-level: the "
    "same brand applies to every client's reports. The logo is stored inside "
    "the encrypted database and travels with backups."
)

firm_branding = get_branding()

name_col, tagline_col = st.columns(2)
with name_col:
    # Placeholders are examples every user sees, so keep them generic -- a real
    # firm name here reads as the app's owner, not as a prompt to type your own.
    firm_name = st.text_input("Firm name", value=firm_branding.firm_name,
                              placeholder="Your firm name")
with tagline_col:
    tagline = st.text_input("Tagline / address line", value=firm_branding.tagline,
                            placeholder="City, ST · yourfirm.com")

accent_col, logo_col = st.columns(2)
with accent_col:
    accent = st.color_picker("Accent color (headings and rules)",
                             value=firm_branding.accent_hex or "#14141A")
    st.caption("Pick your brand color; black keeps the default look.")
with logo_col:
    uploaded_logo = st.file_uploader(
        "Logo (PNG or JPEG, 2MB max)", type=["png", "jpg", "jpeg"],
        key="brand_logo_upload",
    )
    if firm_branding.logo and not uploaded_logo:
        st.image(firm_branding.logo, caption="Current logo", width=160)

remove_logo = False
if firm_branding.logo:
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
st.subheader("Client deliverable branding")
st.caption(
    "The selected client's identity leads its close packages; your firm stays "
    "visible as the preparer. Client branding is stored inside the encrypted "
    "book. An assistant may suggest text and a color, but only a person can "
    "approve those suggestions or upload a logo."
)

if not client_id:
    st.info("Select a client to set its deliverable branding.")
else:
    client = Client.get_by_id(client_id)
    client_branding = get_client_branding(client_id)
    proposals = pending_client_branding_proposals(client_id)

    for proposal in proposals:
        proposed = []
        if proposal["display_name"] is not None:
            proposed.append(f"Name: {proposal['display_name'] or '(clear)'}")
        if proposal["tagline"] is not None:
            proposed.append(f"Tagline: {proposal['tagline'] or '(clear)'}")
        if proposal["accent_hex"] is not None:
            proposed.append(f"Accent: {proposal['accent_hex'] or '(clear)'}")
        st.warning(
            f"Assistant proposal #{proposal['id']} from "
            f"{proposal['created_by']}: " + " · ".join(proposed)
        )
        if proposal["rationale"]:
            st.caption(f"Reason: {proposal['rationale']}")
        accept_col, dismiss_col, _ = st.columns([1, 1, 4])
        with accept_col:
            if st.button("Accept", key=f"accept_client_brand_{proposal['id']}",
                         type="primary"):
                resolve_client_branding_proposal(client_id, proposal["id"], True)
                st.session_state.client_branding_message = "Branding proposal accepted."
                st.rerun()
        with dismiss_col:
            if st.button("Dismiss", key=f"dismiss_client_brand_{proposal['id']}"):
                resolve_client_branding_proposal(client_id, proposal["id"], False)
                st.session_state.client_branding_message = "Branding proposal dismissed."
                st.rerun()

    client_message = st.session_state.pop("client_branding_message", None)
    if client_message:
        st.success(client_message)

    effective_name = ((client.dba_name or client.name) if client else "")
    client_name_col, client_tagline_col = st.columns(2)
    with client_name_col:
        client_display_name = st.text_input(
            "Client display name",
            value=client_branding.display_name,
            placeholder=effective_name,
            key=f"client_brand_name_{client_id}",
        )
    with client_tagline_col:
        client_tagline = st.text_input(
            "Client tagline / address line",
            value=client_branding.tagline,
            placeholder="Optional client tagline or location",
            key=f"client_brand_tagline_{client_id}",
        )

    client_accent_col, client_logo_col = st.columns(2)
    with client_accent_col:
        inherit_firm_accent = st.checkbox(
            "Use the firm's accent color",
            value=not bool(client_branding.accent_hex),
            key=f"client_brand_inherit_accent_{client_id}",
        )
        if inherit_firm_accent:
            client_accent = ""
            inherited = firm_branding.accent_hex or "#14141A"
            st.caption(f"Client headings will follow the firm color ({inherited}).")
        else:
            client_accent = st.color_picker(
                "Client accent color",
                value=(client_branding.accent_hex or firm_branding.accent_hex
                       or "#14141A"),
                key=f"client_brand_accent_{client_id}",
            )
    with client_logo_col:
        client_logo_upload = st.file_uploader(
            "Client logo (PNG or JPEG, 2MB max)",
            type=["png", "jpg", "jpeg"],
            key=f"client_brand_logo_{client_id}",
        )
        if client_branding.logo and not client_logo_upload:
            st.image(client_branding.logo, caption="Current client logo", width=160)

    remove_client_logo = False
    if client_branding.logo:
        remove_client_logo = st.checkbox(
            "Remove the saved client logo", value=False,
            key=f"remove_client_brand_logo_{client_id}",
        )

    if st.button("Save client branding", type="primary",
                 key=f"save_client_branding_{client_id}"):
        client_logo_bytes = None
        client_logo_mime = None
        if client_logo_upload is not None:
            client_logo_bytes = client_logo_upload.getvalue()
            client_logo_mime = client_logo_upload.type
            if client_logo_mime not in ALLOWED_LOGO_MIME:
                st.error("The client logo must be a PNG or JPEG.")
                st.stop()
            if len(client_logo_bytes) > MAX_LOGO_BYTES:
                st.error("The client logo must be 2MB or smaller.")
                st.stop()
        try:
            save_client_branding(
                client_id=client_id,
                display_name=client_display_name,
                tagline=client_tagline,
                accent_hex=client_accent,
                logo=client_logo_bytes,
                logo_mime=client_logo_mime,
                keep_existing_logo=not remove_client_logo,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(
                "Client branding saved. The next close package will use both "
                "the client and firm identities."
            )
            if remove_client_logo and not client_logo_upload:
                st.rerun()

st.divider()
st.subheader("AI categorization")
st.caption(
    "Powered by your own Anthropic API key, stored in the system credential "
    "vault — never in a file. When suggestions run, transaction dates, "
    "descriptions, amounts, account names/numbers, client entity and business "
    "types, and the client's optional AI business context are sent to "
    "Anthropic's API. General client Notes are not sent. Suggestions only; "
    "nothing posts without review."
)

from config import ANTHROPIC_API_KEY
from utils.secure_store import delete_secret, get_secret, set_secret

_saved_key = get_secret("anthropic_api_key")
if ANTHROPIC_API_KEY:
    st.success("AI categorization is enabled for this session.")
elif _saved_key:
    st.info("An API key is saved. Restart LedgerTB to enable AI categorization.")
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
            st.success("Saved to the system credential vault. Restart LedgerTB to enable.")
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
