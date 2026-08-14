"""Network-free update guidance and privacy-safe support links."""

import streamlit as st

from config import APP_VERSION
from utils.client_selector import apply_sidebar_style
from utils import icons


RELEASE_URL = "https://github.com/charliebarmore/LedgerTB/releases/latest"
BUG_URL = (
    "https://github.com/charliebarmore/LedgerTB/issues/new"
    "?template=bug_report.yml"
)
FEATURE_URL = (
    "https://github.com/charliebarmore/LedgerTB/issues/new"
    "?template=feature_request.yml"
)
SECURITY_URL = "https://github.com/charliebarmore/LedgerTB/security/policy"


st.set_page_config(
    page_title="Help & Updates",
    page_icon=icons.HELP,
    layout="wide",
)
apply_sidebar_style()

st.sidebar.page_link("app.py", label="Back to LedgerTB", icon=icons.DASHBOARD)

st.title("Help & Updates")
st.caption(f"Installed version: LedgerTB {APP_VERSION}")

st.info(
    "LedgerTB does not contact GitHub or check for updates in the background. "
    "The buttons below open public GitHub pages in your browser only when you "
    "choose them. No book or client data is included."
)

st.subheader("Get the latest version")
st.markdown(
    "Open the latest release page to compare its version with the installed "
    "version shown above."
)
st.link_button(
    "View latest release",
    RELEASE_URL,
    type="primary",
    icon=":material/open_in_new:",
)

st.markdown(
    "**macOS:** download `LedgerTB-mac.zip`, unzip it, and replace LedgerTB "
    "in Applications.\n\n"
    "**Windows:** download and run `LedgerTB-windows-x64-setup.exe`; the "
    "installer upgrades the existing application.\n\n"
    "Books and backups are stored separately from the application, so replacing "
    "LedgerTB does not remove them. Restart any connected MCP client after an "
    "upgrade so it launches the new executable."
)

st.divider()
st.subheader("Feedback and support")
st.markdown(
    "GitHub provides guided forms so reports arrive with the version, operating "
    "system, reproduction steps, and desired outcome needed to act on them."
)

feedback_columns = st.columns(2)
with feedback_columns[0]:
    st.link_button(
        "Report a bug",
        BUG_URL,
        icon=":material/bug_report:",
        width="stretch",
    )
with feedback_columns[1]:
    st.link_button(
        "Request a feature",
        FEATURE_URL,
        icon=":material/lightbulb:",
        width="stretch",
    )

st.warning(
    "Never attach a real book, bank statement, credential, API key, tax "
    "identifier, client information, or unredacted screenshot or log. Reproduce "
    "the problem with synthetic data. Do not report a suspected security "
    "vulnerability in a public issue."
)
st.link_button(
    "Private security-report instructions",
    SECURITY_URL,
    icon=":material/security:",
)

st.caption(
    "LedgerTB is open-source software without guaranteed response times or "
    "professional accounting support. Accounting, tax, legal, compliance, and "
    "client-specific conclusions remain matters of professional judgment."
)
