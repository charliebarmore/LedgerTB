"""Account-level close readiness: support, explanations, notes, and signoff."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from database import connection as dbconn
from models import close_map
from models.client import Client
from models.fiscal_period import FiscalPeriod
from money import to_dollars
from utils import icons
from utils.client_context import scope_page_to_client
from utils.client_selector import render_client_selector
from utils.fiscal_dates import fiscal_year_ending_year
from utils.unlock import require_unlock


st.set_page_config(page_title="Close Map", page_icon=icons.CLOSE_MAP, layout="wide")
require_unlock()
init_database()
client_id = render_client_selector()

st.title("Close Map")
if not client_id:
    st.warning("Please select or create a client first.")
    st.stop()

client = Client.get_by_id(client_id)
st.caption(
    f"Viewing: **{client.name}** · Support, explain, and sign off each balance "
    "before the year is closed."
)

# Period and account ids restart in every book, and note ids can collide
# across clients. Widget state that names one of those ids must be owned by
# (book, client) or a switch can silently keep the previous selection — or
# hand a typed-but-unsaved resolution to another client's same-numbered
# note. The scope generation rotates the keys, the only reset the browser
# honors.
close_map_scope = scope_page_to_client(
    st.session_state, "close_map", client_id, dbconn.DATABASE_PATH
)
close_map_key = close_map_scope.key

periods = FiscalPeriod.get_all(client_id, period_type="Year")
if not periods:
    FiscalPeriod.ensure_periods_exist(
        client_id,
        fiscal_year_ending_year(date.today(), client.fiscal_year_end_month),
        client.fiscal_year_end_month,
    )
    periods = FiscalPeriod.get_all(client_id, period_type="Year")

period_by_id = {period.id: period for period in periods}
period_id = st.selectbox(
    "Fiscal year",
    options=list(period_by_id),
    format_func=lambda pid: (
        f"{period_by_id[pid].period_name} "
        f"({period_by_id[pid].start_date:%m/%d/%Y} – "
        f"{period_by_id[pid].end_date:%m/%d/%Y})"
    ),
    key=close_map_key("period"),
)
period = period_by_id[period_id]
summary = close_map.readiness(client_id, period_id)

metric_cols = st.columns(5)
metric_cols[0].metric("Required", summary["required_count"])
metric_cols[1].metric("Reviewed", summary["reviewed_count"])
metric_cols[2].metric("Prepared", summary["counts"][close_map.PREPARED])
metric_cols[3].metric("Changed", summary["counts"][close_map.CHANGED])
metric_cols[4].metric("Exceptions", summary["counts"][close_map.EXCEPTION])

if summary["ready"]:
    st.success("All required balances are reviewed and current.")
elif summary["rows"]:
    st.warning(
        f"{summary['incomplete_count']} required "
        f"{'balance needs' if summary['incomplete_count'] == 1 else 'balances need'} "
        "attention before this Close Map is ready."
    )
else:
    st.info("No current or prior-year balances need review for this fiscal year.")

with st.expander("Lead-sheet groups"):
    groups = close_map.list_groups(client_id)
    if groups:
        st.caption(" · ".join(f"{group['code']} — {group['name']}" for group in groups))
    else:
        st.caption("No groups yet. Add the codes your firm uses for its lead sheets.")
    with st.form("add_close_group", clear_on_submit=True):
        gc1, gc2, gc3 = st.columns([1, 3, 1], vertical_alignment="bottom")
        group_code = gc1.text_input("Code", placeholder="A")
        group_name = gc2.text_input("Name", placeholder="Cash")
        add_group = gc3.form_submit_button("Add group", width="stretch")
        if add_group:
            try:
                close_map.create_group(client_id, group_code, group_name)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.rerun()
    if groups:
        with st.form("edit_close_group"):
            edit_group_id = st.selectbox(
                "Edit group", [group["id"] for group in groups],
                format_func=lambda gid: next(
                    f"{group['code']} — {group['name']}"
                    for group in groups if group["id"] == gid
                ),
            )
            edit_group = next(group for group in groups if group["id"] == edit_group_id)
            eg1, eg2, eg3 = st.columns([1, 3, 1], vertical_alignment="bottom")
            edit_code = eg1.text_input("Updated code", value=edit_group["code"])
            edit_name = eg2.text_input("Updated name", value=edit_group["name"])
            if eg3.form_submit_button("Save group", width="stretch"):
                try:
                    close_map.update_group(
                        client_id, edit_group_id, edit_code, edit_name
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        with st.form("bulk_close_group"):
            bulk_accounts = st.multiselect(
                "Assign accounts in bulk",
                [row.account_id for row in summary["rows"]],
                format_func=lambda aid: next(
                    f"{row.account_number} — {row.account_name}"
                    for row in summary["rows"] if row.account_id == aid
                ),
            )
            bulk_group = st.selectbox(
                "Assign to", [group["id"] for group in groups],
                format_func=lambda gid: next(
                    f"{group['code']} — {group['name']}"
                    for group in groups if group["id"] == gid
                ),
            )
            if st.form_submit_button("Assign selected accounts"):
                try:
                    close_map.bulk_assign_group(
                        client_id, bulk_accounts, bulk_group
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

if not summary["rows"]:
    st.stop()

filter_cols = st.columns([2, 2, 3])
status_options = ["All"] + [
    close_map.NOT_STARTED, close_map.IN_PROGRESS, close_map.PREPARED,
    close_map.REVIEWED, close_map.CHANGED, close_map.EXCEPTION,
    close_map.NOT_REQUIRED,
]
status_filter = filter_cols[0].selectbox("Status", status_options)
group_options = ["All", "Unassigned"] + [
    f"{group['code']} — {group['name']}" for group in close_map.list_groups(client_id)
]
group_filter = filter_cols[1].selectbox("Lead sheet", group_options)
search = filter_cols[2].text_input("Find account", placeholder="Number or name")

filtered = list(summary["rows"])
if status_filter != "All":
    filtered = [row for row in filtered if row.status == status_filter]
if group_filter == "Unassigned":
    filtered = [row for row in filtered if not row.group_code]
elif group_filter != "All":
    code = group_filter.split(" — ", 1)[0]
    filtered = [row for row in filtered if row.group_code == code]
if search.strip():
    needle = search.strip().lower()
    filtered = [
        row for row in filtered
        if needle in row.account_number.lower() or needle in row.account_name.lower()
    ]

def _money(value):
    return f"{value:,.2f}"


table = pd.DataFrame([
    {
        "Acct #": row.account_number,
        "Account": row.account_name,
        "Adjusted": _money(row.current_balance),
        "PY": _money(row.prior_balance),
        "$ Change": _money(row.change),
        "% Change": "—" if row.change_percent is None else f"{row.change_percent:,.1f}%",
        "Lead Sheet": (f"{row.group_code} — {row.group_name}" if row.group_code else "—"),
        "Evidence": row.evidence_count,
        "Open Notes": row.open_note_count,
        "Status": row.status,
    }
    for row in filtered
])
st.dataframe(table, hide_index=True, width="stretch", height=min(540, 38 * len(table) + 40))

if not filtered:
    st.info("No accounts match these filters.")
    st.stop()

account_ids = [row.account_id for row in filtered]
row_by_id = {row.account_id: row for row in filtered}
selected_account_id = st.selectbox(
    "Review account",
    account_ids,
    format_func=lambda aid: (
        f"{row_by_id[aid].account_number} — {row_by_id[aid].account_name} "
        f"({row_by_id[aid].status})"
    ),
    key=close_map_key("account"),
)
detail = close_map.account_detail(client_id, period_id, selected_account_id)
row = detail["row"]

st.divider()
st.subheader(f"{row.account_number} — {row.account_name}")
balance_cols = st.columns(5)
balance_cols[0].metric("Adjusted", f"${row.current_balance:,.2f}")
balance_cols[1].metric("Prior year", f"${row.prior_balance:,.2f}")
balance_cols[2].metric("Change", f"${row.change:,.2f}")
balance_cols[3].metric(
    "% change", "—" if row.change_percent is None else f"{row.change_percent:,.1f}%"
)
balance_cols[4].metric("AJE effect", f"${to_dollars(detail['snapshot']['aje_cents']):,.2f}")
st.caption(
    f"{detail['snapshot']['ledger_line_count']} ledger lines through "
    f"{summary['period_end']} · Status: {row.status}"
)

prior_context = detail["prior_year_context"]
if prior_context:
    with st.expander(
        f"Prior-year review context — {prior_context['period_name']}",
        expanded=not bool(row.explanation),
    ):
        st.caption(
            "Reference only. The lead-sheet mapping carries forward, but prior-year "
            "evidence and signoffs do not count for this fiscal year. Add current "
            "support and complete fresh preparer and reviewer signoffs below."
        )
        if not prior_context["had_review"]:
            st.info("No account review was recorded for the prior fiscal year.")
        else:
            st.markdown("**Prior-year explanation**")
            st.write(prior_context["explanation"] or "No explanation was recorded.")

            prior_support, prior_notes = st.columns(2)
            with prior_support:
                st.markdown("**Prior-year support**")
                if prior_context["evidence"]:
                    for item in prior_context["evidence"]:
                        st.markdown(
                            f"- **{item['reference']}** · "
                            f"{item['evidence_type'].replace('ledgerpdf', 'LedgerPDF').title()}"
                            f" — {item['description'] or 'No description'}"
                        )
                else:
                    st.caption("No evidence references were recorded.")
            with prior_notes:
                st.markdown("**Prior-year review notes**")
                if prior_context["notes"]:
                    for note in prior_context["notes"]:
                        resolution = (
                            f" — Resolved: {note['resolution']}"
                            if note["status"] == "resolved" else " — Open"
                        )
                        st.markdown(f"- {note['body']}{resolution}")
                else:
                    st.caption("No review notes were recorded.")

            signoff_parts = []
            if prior_context["prepared_by"]:
                signoff_parts.append(
                    f"Prepared by {prior_context['prepared_by']} · "
                    f"{prior_context['prepared_at']}"
                )
            if prior_context["reviewed_by"]:
                signoff_parts.append(
                    f"Reviewed by {prior_context['reviewed_by']} · "
                    f"{prior_context['reviewed_at']}"
                )
            if signoff_parts:
                st.caption("  \n".join(signoff_parts))

groups = close_map.list_groups(client_id)
group_ids = [None] + [group["id"] for group in groups]
group_labels = {None: "Unassigned"}
group_labels.update({group["id"]: f"{group['code']} — {group['name']}" for group in groups})
with st.form(f"close_review_{selected_account_id}"):
    group_id = st.selectbox(
        "Lead-sheet group", group_ids, format_func=lambda gid: group_labels[gid],
        index=group_ids.index(row.group_id) if row.group_id in group_ids else 0,
    )
    required = st.checkbox("Review required", value=row.required)
    exclusion_reason = st.text_input(
        "Reason review is not required", value=row.exclusion_reason,
        disabled=required,
    )
    explanation = st.text_area(
        "Balance and variance explanation", value=row.explanation,
        placeholder="What supports this balance, and what explains the change from prior year?",
        height=120,
    )
    if st.form_submit_button("Save review details", type="primary"):
        try:
            close_map.save_mapping(
                client_id, selected_account_id, group_id, required, exclusion_reason
            )
            close_map.save_explanation(client_id, period_id, selected_account_id, explanation)
        except Exception as exc:
            st.error(str(exc))
        else:
            st.rerun()

if detail["proposals"]:
    st.markdown("#### Assistant proposals")
    for proposal in detail["proposals"]:
        st.info(
            f"{proposal['explanation']}\n\n"
            f"Rationale: {proposal['rationale'] or 'Not provided'} · {proposal['created_by']}"
        )
        pc1, pc2, _ = st.columns([1, 1, 4])
        if pc1.button("Accept", key=f"accept_close_proposal_{proposal['id']}"):
            close_map.resolve_proposal(client_id, proposal["id"], True)
            st.rerun()
        if pc2.button("Dismiss", key=f"dismiss_close_proposal_{proposal['id']}"):
            close_map.resolve_proposal(client_id, proposal["id"], False)
            st.rerun()

evidence_col, notes_col = st.columns(2)
with evidence_col:
    st.markdown("#### Evidence")
    for item in detail["evidence"]:
        e1, e2 = st.columns([5, 1])
        e1.markdown(
            f"**{item['reference']}** · {item['evidence_type'].title()}  \n"
            f"{item['description'] or 'No description'} · {item['created_by']}"
        )
        if e2.button("Remove", key=f"remove_evidence_{item['id']}"):
            close_map.remove_evidence(client_id, item["id"])
            st.rerun()
    with st.form(f"add_evidence_{selected_account_id}", clear_on_submit=True):
        evidence_type = st.selectbox(
            "Type", ["workpaper", "ledgerpdf", "external", "reconciliation"],
            format_func=lambda value: value.replace("ledgerpdf", "LedgerPDF").title(),
        )
        reference = st.text_input("Reference", placeholder="A-1")
        description = st.text_input("Description", placeholder="Year-end bank reconciliation")
        if st.form_submit_button("Add evidence"):
            try:
                close_map.add_evidence(
                    client_id, period_id, selected_account_id,
                    evidence_type, reference, description,
                )
            except Exception as exc:
                st.error(str(exc))
            else:
                st.rerun()
    if detail["reconciliations"]:
        latest = detail["reconciliations"][0]
        st.caption(
            f"Latest reconciliation: {latest['statement_end_date']} · "
            f"{latest['status']} · statement balance "
            f"${to_dollars(latest['statement_ending_balance']):,.2f}"
        )

with notes_col:
    st.markdown("#### Review notes")
    open_notes = [note for note in detail["notes"] if note["status"] == "open"]
    resolved_notes = [note for note in detail["notes"] if note["status"] == "resolved"]
    for note in open_notes:
        st.warning(f"{note['body']} · {note['created_by']}")
        with st.form(f"resolve_note_{note['id']}"):
            resolution = st.text_input("Resolution", key=close_map_key(f"resolution_{note['id']}"))
            if st.form_submit_button("Resolve note"):
                try:
                    close_map.resolve_note(client_id, note["id"], resolution)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
    with st.form(f"add_note_{selected_account_id}", clear_on_submit=True):
        note_body = st.text_area("New review note", height=90)
        if st.form_submit_button("Add note"):
            try:
                close_map.add_note(client_id, period_id, selected_account_id, note_body)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.rerun()
    if resolved_notes:
        with st.expander(f"Resolved notes ({len(resolved_notes)})"):
            for note in resolved_notes:
                st.markdown(
                    f"~~{note['body']}~~  \nResolved: {note['resolution']} · "
                    f"{note['resolved_by']}"
                )

st.markdown("#### Signoff")
if row.prepared_by:
    st.caption(f"Latest preparer signoff: {row.prepared_by} · {row.prepared_at}")
if row.reviewed_by:
    st.caption(f"Latest reviewer signoff: {row.reviewed_by} · {row.reviewed_at}")
if row.status == close_map.CHANGED:
    st.warning("The balance or its support changed after signoff. Prepare and review it again.")
prepare_blockers = []
if row.required:
    if not row.explanation.strip():
        prepare_blockers.append("save a current-year balance and variance explanation")
    if not detail["evidence"]:
        prepare_blockers.append("add at least one current-year evidence reference")
    if row.open_note_count:
        prepare_blockers.append("resolve all open review notes")
if prepare_blockers:
    st.caption("Before preparation: " + "; ".join(prepare_blockers) + ".")
sign1, sign2, _ = st.columns([1, 1, 4])
if sign1.button(
    "Mark prepared", type="primary",
    disabled=(not row.required or bool(prepare_blockers)),
):
    try:
        close_map.signoff(client_id, period_id, selected_account_id, "preparer")
    except Exception as exc:
        st.error(str(exc))
    else:
        st.rerun()
if sign2.button(
    "Mark reviewed", disabled=(not row.required or row.status != close_map.PREPARED)
):
    try:
        close_map.signoff(client_id, period_id, selected_account_id, "reviewer")
    except Exception as exc:
        st.error(str(exc))
    else:
        st.rerun()
