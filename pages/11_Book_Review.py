import streamlit as st
import sys
import hashlib
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from database import connection as dbconn
from models.audit_log import AuditLog
from models.client import Client
from services.book_review import (
    book_review_service,
    compute_analytics,
    get_review_policy,
    run_integrity_sweep,
    set_review_policy,
)
from utils.client_selector import render_client_selector
from utils.fiscal_dates import fiscal_year_bounds
from utils.unlock import require_unlock
from utils.untrusted import defang_markdown
from utils import icons

st.set_page_config(page_title="Book Review", page_icon=icons.REVIEW, layout="wide")

require_unlock()
init_database()

client_id = render_client_selector()

st.title("Book Review")
st.caption(
    "Are these books actually right? Mechanical checks run locally and cost "
    "nothing. The AI reviews use your Anthropic key and send transaction "
    "dates, descriptions, amounts, and account names — suggestions only, "
    "nothing posts without you."
)

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

client = Client.get_by_id(client_id)
fy_start, _ = fiscal_year_bounds(date.today(), client.fiscal_year_end_month)

# Client ids restart at 1 in every book. Use the full book/client owner for
# stored results, and a compact deterministic token for Streamlit widget keys,
# so switching books cannot carry policy text or period inputs into a different
# client that happens to have the same numeric id.
review_owner = (str(dbconn.DATABASE_PATH), client_id)
book_token = hashlib.sha256(
    str(dbconn.DATABASE_PATH).encode("utf-8")
).hexdigest()[:12]
review_widget_scope = f"{book_token}_{client_id}"

period_cols = st.columns([1, 1, 2])
with period_cols[0]:
    period_start = st.date_input(
        "From", value=fy_start, key=f"review_start_{review_widget_scope}"
    )
with period_cols[1]:
    period_end = st.date_input(
        "To", value=date.today(), key=f"review_end_{review_widget_scope}"
    )
if period_start > period_end:
    st.error("The review period start cannot be after its end.")
    st.stop()
period_label = f"{period_start.isoformat()} to {period_end.isoformat()}"

# ------------------------------------------------------------- policy notes
with st.expander("Accounting policy notes (the reviewer honors these)"):
    st.caption(
        "Your house rules, written once and enforced every review — e.g. "
        "\"ADP and Gusto fees go to 7080, never Dues. GoDaddy is software. "
        "Cash rewards are 5002 Income - Other.\""
    )
    # Keyed per book and client: a keyed widget ignores value= once it holds
    # state, so a shared key would carry one client's notes into another
    # editor and the Save button would write them onto that client.
    policy_text = st.text_area(
        "Policy notes", value=get_review_policy(client_id),
        key=f"review_policy_text_{review_widget_scope}", height=140,
        label_visibility="collapsed",
    )
    if st.button("Save policy notes"):
        set_review_policy(client_id, policy_text)
        st.success("Saved. Every future review will honor these notes.")

SEVERITY_BADGE = {"high": ":red[HIGH]", "medium": ":orange[MEDIUM]", "info": ":gray[INFO]"}


def render_findings(findings, allow_jump=False):
    for i, finding in enumerate(findings):
        cols = st.columns([5, 1]) if (allow_jump and finding.entry_id) else [st.container()]
        with cols[0]:
            st.markdown(f"{SEVERITY_BADGE.get(finding.severity, '')} **{finding.title}**")
            detail = finding.detail or ""
            if finding.suggested_account_number:
                suggestion = (f"Suggest **{finding.suggested_account_number} - "
                              f"{finding.suggested_account_name or ''}**".rstrip())
                detail = f"{detail}  \n{suggestion}" if detail else suggestion
            if detail:
                st.markdown(defang_markdown(detail))
        if allow_jump and finding.entry_id:
            with cols[1]:
                if st.button("Review entry", key=f"review_jump_{finding.skill}_{i}"):
                    # The Journal Entries page routes import-linked entries to
                    # the guided category correction automatically.
                    st.session_state.edit_entry_id = finding.entry_id
                    st.switch_page("pages/2_Journal_Entries.py")


# ---------------------------------------------------------- integrity sweep
st.subheader("Integrity sweep")
integrity = run_integrity_sweep(client_id, period_start, period_end)
if integrity:
    high = sum(1 for f in integrity if f.severity == "high")
    if high:
        st.error(f"{high} high-severity issue{'s' if high != 1 else ''} found.")
    render_findings(integrity)
else:
    st.success("No integrity issues found — balanced, complete, and linked.")

st.divider()

# ---------------------------------------------------- AI category consistency
st.subheader("Category consistency review")
if not book_review_service.is_available():
    st.caption("AI reviews are off — add your Anthropic API key on the Firm "
               "Settings page, then restart LedgerTB.")
    st.page_link("pages/12_Firm_Settings.py", label="Set up AI", icon=icons.FIRM)
else:
    if st.button("Review categorizations", type="primary"):
        with st.spinner("Reading the period's transactions…"):
            findings, reviewed = book_review_service.review_categories(
                client_id, period_start, period_end,
                policy_notes=get_review_policy(client_id),
            )
        if book_review_service.last_error:
            st.error(f"The review call failed: {book_review_service.last_error}")
        else:
            st.session_state.category_review = (
                review_owner, findings, reviewed, period_label
            )
            AuditLog.log_event(client_id, "REVIEW", "category_consistency_review", {
                "period_start": period_start, "period_end": period_end,
                "transactions_reviewed": reviewed,
                "findings": len(findings),
            })
    stored = st.session_state.get("category_review")
    # Results belong to the client they were run for; never show or export
    # one client's review under another client's name.
    if stored and stored[0] == review_owner:
        _, findings, reviewed, stored_label = stored
        st.caption(f"Reviewed {reviewed} posted transactions ({stored_label}).")
        if findings:
            render_findings(findings, allow_jump=True)
        else:
            st.success("No miscategorizations flagged.")

st.divider()

# ------------------------------------------------------------------ analytics
st.subheader("Analytics")
analytics = compute_analytics(client_id, period_start, period_end)
metric_cols = st.columns(4)
metric_cols[0].metric("Revenue", f"${analytics['revenue']:,.2f}")
metric_cols[1].metric("Expenses", f"${analytics['expenses']:,.2f}")
metric_cols[2].metric("Net income", f"${analytics['net_income']:,.2f}",
                      delta=f"{analytics['net_margin_pct']:.1f}% margin",
                      delta_color="off")
metric_cols[3].metric("Months of expenses in cash",
                      f"{analytics['months_of_expenses_in_cash']:.1f}")

detail_cols = st.columns(2)
with detail_cols[0]:
    st.markdown("**Top expenses**")
    for row in analytics["top_expenses"]:
        c1, c2 = st.columns([3, 1])
        c1.text(row["label"][:38])
        pct = (row["value"] / analytics["revenue"] * 100) if analytics["revenue"] else 0
        c2.text(f"${row['value']:,.2f} ({pct:.0f}%)")
with detail_cols[1]:
    st.markdown("**By month**")
    for m in analytics["monthly"]:
        c1, c2, c3 = st.columns([1, 1, 1])
        c1.text(m["month"])
        c2.text(f"in {m['revenue']:,.2f}")
        c3.text(f"out {m['expenses']:,.2f}")

if book_review_service.is_available():
    if st.button("Write analytical memo"):
        with st.spinner("Writing the reviewer's memo…"):
            memo = book_review_service.write_analytics_memo(
                client.name, period_label, analytics,
                policy_notes=get_review_policy(client_id),
            )
        if book_review_service.last_error:
            st.error(f"The memo call failed: {book_review_service.last_error}")
        elif memo:
            st.session_state.analytics_memo = (
                review_owner, memo, period_label
            )
            AuditLog.log_event(client_id, "REVIEW", "analytical_review_memo", {
                "period_start": period_start, "period_end": period_end,
            })
    stored_memo = st.session_state.get("analytics_memo")
    if stored_memo and stored_memo[0] == review_owner:
        _, memo, memo_label = stored_memo
        st.markdown(f"**Analytical review memo** · {memo_label}")
        st.markdown(defang_markdown(memo))
        st.download_button(
            "Download memo",
            data=f"# Analytical review — {client.name}\n{memo_label}\n\n{memo}\n",
            file_name=f"AnalyticalReview_{client.name}_{period_end.strftime('%Y%m%d')}.md",
        )
