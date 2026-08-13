"""Legal and data-practice disclosures that remain available while locked."""
import streamlit as st

from config import APP_VERSION
from utils.client_selector import apply_sidebar_style
from utils import icons


st.set_page_config(
    page_title="Legal & Disclosures",
    page_icon=icons.LEGAL,
    layout="wide",
)
apply_sidebar_style()

st.sidebar.page_link("app.py", label="Back to LedgerTB", icon=icons.DASHBOARD)

st.title("Legal & Disclosures")
st.caption(f"LedgerTB {APP_VERSION} · Effective August 13, 2026")

st.warning(
    "LedgerTB is general-purpose software, not accounting, tax, legal, "
    "investment, audit, assurance, or other professional advice or services. "
    "You remain responsible for the books, the protection of client data, "
    "and every conclusion or decision made using LedgerTB."
)

st.subheader("Professional responsibility")
st.markdown(
    "Using LedgerTB does not create a CPA-client, accountant-client, "
    "attorney-client, fiduciary, or other professional relationship with "
    "Ledger Labs LLC, Charlie Barmore, or a contributor. Double-entry checks, "
    "review workflows, reports, and audit history do not prove that activity "
    "is complete, accurate, appropriate, or compliant. A balanced trial "
    "balance is not proof that every transaction was imported or recorded."
)
st.markdown(
    "You are responsible for validating imports, classifications, entries, "
    "reconciliations, reports, close packages, and AI output; maintaining "
    "internal controls and independent recovery copies; protecting devices, "
    "passphrases, keys, files, and export folders; and complying with laws, "
    "professional standards, contracts, and firm policies."
)

st.subheader("AI and MCP data")
st.markdown(
    "AI output can be incomplete, inaccurate, or inappropriate. At the "
    "**post** MCP level, an assistant may append balanced entries without "
    "prior approval. A person remains responsible for reviewing that work, "
    "making corrections through visible new entries, and recording signoff."
)
st.info(
    "LedgerTB has no Ledger Labs cloud bookkeeping backend. The book remains "
    "in the active encrypted file, but optional AI features and MCP clients "
    "may send selected data to the provider you configure. Approve that "
    "provider and its retention, training, confidentiality, security, and "
    "data-handling terms before using client information."
)

st.subheader("Security, backups, and availability")
st.markdown(
    "Encryption, loopback binding, permission controls, audit history, and "
    "backups reduce risk; they do not guarantee confidentiality, integrity, "
    "availability, or recovery. Use a supported release, install security "
    "updates, and keep tested recovery copies outside the primary device or "
    "storage location. Ledger Labs LLC does not promise uninterrupted "
    "operation, future updates, universal compatibility, or data recovery."
)

st.subheader("No warranty")
st.markdown(
    "**LEDGERTB IS PROVIDED “AS IS” AND “WITH ALL FAULTS,” WITHOUT WARRANTIES "
    "OR CONDITIONS OF ANY KIND, TO THE FULLEST EXTENT PERMITTED BY LAW.** "
    "The MIT License's warranty disclaimer and limitation of liability apply. "
    "Some jurisdictions preserve rights that cannot be waived."
)

st.divider()
links = st.columns(3)
with links[0]:
    st.link_button(
        "Read the full Disclaimer",
        "https://ledgertb.com/disclaimer.html",
        width="stretch",
    )
with links[1]:
    st.link_button(
        "Privacy & Data Practices",
        "https://ledgertb.com/privacy.html",
        width="stretch",
    )
with links[2]:
    st.link_button(
        "Website and Distribution Terms",
        "https://ledgertb.com/terms.html",
        width="stretch",
    )

st.caption(
    "Questions: info@ledgerlabs.co · Security reports: "
    "github.com/charliebarmore/LedgerTB/security/policy"
)
