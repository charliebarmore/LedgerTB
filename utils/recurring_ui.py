"""Streamlit rendering for journal-entry templates and recurring schedules."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from database import connection as dbconn
from models.account import Account
from models.fiscal_period import FiscalPeriod
from models.recurring_entry import (
    JournalEntryTemplate,
    RecurringSchedule,
    TemplateLine,
)
from money import to_cents
from services.recurring_entries import (
    generate_selected,
    occurrence_history,
    preview_due,
    rejected_recoveries,
    regenerate_occurrence,
    skip_occurrence,
    undo_skip,
)
from utils.dates import display_date
from utils.fiscal_dates import fiscal_year_ending_year


def _key(base: str) -> str:
    """Rotate browser-owned widget identity when the book/client changes."""
    return f"{base}_rg{st.session_state.get('recurring_widget_gen', 0)}"


def _open_template_editor(template: JournalEntryTemplate | None, accounts: list) -> None:
    by_id = {account.id: account.display_name() for account in accounts}
    st.session_state.recurring_editor_id = template.id if template else 0
    st.session_state.recurring_editor_name = template.name if template else ""
    st.session_state.recurring_editor_description = (
        template.description if template else ""
    )
    st.session_state.recurring_editor_reference = (
        template.source_reference if template else ""
    )
    st.session_state.recurring_editor_type = (
        template.entry_type if template else "Regular"
    )
    st.session_state.recurring_editor_rows = (
        [
            {
                "Account": by_id.get(line.account_id, ""),
                "Debit": line.debit_cents / 100 if line.debit_cents else 0.0,
                "Credit": line.credit_cents / 100 if line.credit_cents else 0.0,
                "Memo": line.memo or "",
            }
            for line in template.lines
        ]
        if template else [
            {"Account": "", "Debit": 0.0, "Credit": 0.0, "Memo": ""},
            {"Account": "", "Debit": 0.0, "Credit": 0.0, "Memo": ""},
        ]
    )
    st.session_state.recurring_editor_gen = (
        st.session_state.get("recurring_editor_gen", 0) + 1
    )


def _close_template_editor() -> None:
    for key in (
        "recurring_editor_id", "recurring_editor_name",
        "recurring_editor_description", "recurring_editor_reference",
        "recurring_editor_type", "recurring_editor_rows",
    ):
        st.session_state.pop(key, None)


def _value(value, default=0.0):
    return default if value is None or pd.isna(value) else value


def _render_template_editor(client_id: int, accounts: list) -> None:
    editor_id = st.session_state.get("recurring_editor_id")
    if editor_id is None:
        return
    gen = st.session_state.get("recurring_editor_gen", 0)
    account_labels = {account.display_name(): account.id for account in accounts}

    with st.container(border=True):
        st.subheader("Create template" if editor_id == 0 else "Edit template")
        name = st.text_input(
            "Template name", value=st.session_state.recurring_editor_name,
            key=_key(f"recurring_editor_name_g{gen}"),
        )
        description = st.text_input(
            "Journal-entry description",
            value=st.session_state.recurring_editor_description,
            key=_key(f"recurring_editor_description_g{gen}"),
        )
        header_left, header_right = st.columns(2)
        with header_left:
            entry_type = st.selectbox(
                "Entry type", ["Regular", "Adjusting"],
                index=0 if st.session_state.recurring_editor_type == "Regular" else 1,
                key=_key(f"recurring_editor_type_g{gen}"),
            )
        with header_right:
            reference = st.text_input(
                "Source-reference default",
                value=st.session_state.recurring_editor_reference,
                key=_key(f"recurring_editor_reference_g{gen}"),
            )
        st.caption("Amounts are fixed for v1.7.0. Add or remove rows as needed.")
        frame = pd.DataFrame(st.session_state.recurring_editor_rows)
        edited = st.data_editor(
            frame,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            key=_key(f"recurring_editor_lines_g{gen}"),
            column_config={
                "Account": st.column_config.SelectboxColumn(
                    "Account", options=list(account_labels), required=True,
                ),
                "Debit": st.column_config.NumberColumn(
                    "Debit", min_value=0.0, step=0.01, format="$%.2f",
                ),
                "Credit": st.column_config.NumberColumn(
                    "Credit", min_value=0.0, step=0.01, format="$%.2f",
                ),
                "Memo": st.column_config.TextColumn("Memo"),
            },
        )
        save_col, cancel_col, _ = st.columns([1, 1, 3])
        with save_col:
            save = st.button(
                "Save template", type="primary", key=_key(f"recurring_editor_save_g{gen}"),
                disabled=dbconn.READ_ONLY,
            )
        with cancel_col:
            cancel = st.button("Cancel", key=_key(f"recurring_editor_cancel_g{gen}"))
        if cancel:
            _close_template_editor()
            st.rerun()
        if save:
            try:
                lines = []
                for row_number, row in enumerate(edited.to_dict("records"), 1):
                    label = str(_value(row.get("Account"), "") or "").strip()
                    debit = float(_value(row.get("Debit")))
                    credit = float(_value(row.get("Credit")))
                    memo = str(_value(row.get("Memo"), "") or "").strip()
                    if not label and not debit and not credit and not memo:
                        continue
                    if label not in account_labels:
                        raise ValueError(f"Choose an account on template line {row_number}.")
                    lines.append(TemplateLine(
                        account_id=account_labels[label],
                        debit_cents=to_cents(debit), credit_cents=to_cents(credit),
                        memo=memo,
                    ))
                if editor_id:
                    template = JournalEntryTemplate.get_by_id(editor_id, client_id)
                    if not template:
                        raise ValueError("Template not found for this client.")
                    template.name = name
                    template.description = description
                    template.entry_type = entry_type
                    template.source_reference = reference
                    template.lines = lines
                    result = "updated"
                else:
                    template = JournalEntryTemplate(
                        client_id=client_id, name=name, description=description,
                        entry_type=entry_type, source_reference=reference,
                        lines=lines,
                    )
                    result = "created"
                template.save()
            except Exception as exc:
                st.error(str(exc))
            else:
                st.session_state.recurring_result = (
                    f"Template {template.name} {result}."
                )
                _close_template_editor()
                st.rerun()


def _render_schedule_editor(
    client_id: int,
    template: JournalEntryTemplate,
    schedule: RecurringSchedule | None,
    date_format: str,
) -> None:
    suffix = template.id
    with st.expander("Recurring schedule", expanded=False):
        frequency_options = ["Monthly", "Quarterly", "Annually"]
        current_frequency = schedule.frequency if schedule else "Monthly"
        frequency = st.selectbox(
            "Frequency", frequency_options,
            index=frequency_options.index(current_frequency),
            key=_key(f"recurring_frequency_{suffix}"),
        )
        rule_options = ["PeriodEnd", "PeriodStart", "DayOfMonth"]
        current_rule = schedule.date_rule if schedule else "PeriodEnd"
        date_rule = st.selectbox(
            "Entry date", rule_options, index=rule_options.index(current_rule),
            format_func=lambda value: {
                "PeriodEnd": "Period end", "PeriodStart": "Period start",
                "DayOfMonth": "Day of month",
            }[value],
            key=_key(f"recurring_date_rule_{suffix}"),
        )
        if date_rule == "DayOfMonth":
            day_of_month = st.number_input(
                "Day", min_value=1, max_value=31,
                value=int(schedule.day_of_month if schedule and schedule.day_of_month else 1),
                step=1, key=_key(f"recurring_day_{suffix}"),
            )
            if frequency != "Monthly":
                st.warning("Day of month is available only for monthly schedules.")
        else:
            day_of_month = None
        dates_left, dates_right = st.columns(2)
        with dates_left:
            starts_on = st.date_input(
                "First applicable date",
                value=schedule.starts_on if schedule else date.today(),
                format=date_format, key=_key(f"recurring_starts_{suffix}"),
            )
        with dates_right:
            has_end = st.checkbox(
                "Last date", value=bool(schedule and schedule.ends_on),
                key=_key(f"recurring_has_end_{suffix}"),
            )
            ends_on = st.date_input(
                "Last applicable date",
                value=(schedule.ends_on if schedule and schedule.ends_on else starts_on),
                format=date_format, key=_key(f"recurring_ends_{suffix}"),
                disabled=not has_end,
                label_visibility="collapsed" if not has_end else "visible",
            )
        reversal_key = _key(f"recurring_reversal_{suffix}")
        if reversal_key not in st.session_state:
            st.session_state[reversal_key] = bool(
                schedule and schedule.reversal_rule == "NextDay"
            )
        if date_rule != "PeriodEnd":
            # Clear before widget instantiation; a keyed checkbox otherwise
            # keeps its old True value while disabled and makes the transition
            # away from a reversing schedule impossible to save.
            st.session_state[reversal_key] = False
        reversal = st.checkbox(
            "Create a reversal draft after the period-end entry posts",
            key=reversal_key,
            disabled=date_rule != "PeriodEnd",
        )
        action_col, pause_col, _ = st.columns([1, 1, 2])
        with action_col:
            if st.button(
                "Save schedule", type="primary",
                key=_key(f"recurring_save_schedule_{suffix}"),
                disabled=dbconn.READ_ONLY,
            ):
                try:
                    target = schedule or RecurringSchedule(template_id=template.id)
                    target.frequency = frequency
                    target.date_rule = date_rule
                    target.day_of_month = int(day_of_month) if day_of_month else None
                    target.starts_on = starts_on
                    target.ends_on = ends_on if has_end else None
                    target.reversal_rule = "NextDay" if reversal else "None"
                    target.save()
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state.recurring_result = (
                        f"Schedule for {template.name} saved."
                    )
                    st.rerun()
        with pause_col:
            if schedule and st.button(
                "Pause" if schedule.is_active else "Resume",
                key=_key(f"recurring_toggle_schedule_{suffix}"),
                disabled=dbconn.READ_ONLY,
            ):
                try:
                    target_active = not schedule.is_active
                    schedule.set_active(target_active)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state.recurring_result = (
                        f"Schedule for {template.name} "
                        f"{'resumed' if target_active else 'paused'}."
                    )
                    st.rerun()


def _render_due(client, date_format: str) -> None:
    st.subheader("Due recurring entries")
    through = st.date_input(
        "Generate through", value=date.today(), format=date_format,
        max_value=date.today() + timedelta(days=3660),
        key=_key("recurring_through_date"),
        help="Choose a future date only when you intentionally want to prepare ahead.",
    )
    try:
        previews = preview_due(client.id, through_date=through)
    except Exception as exc:
        st.error(f"Recurring entries could not be previewed: {exc}")
        return

    due = [item for item in previews if item.state == "Due"]
    blocked = [item for item in previews if item.state == "Blocked"]
    skipped = [item for item in previews if item.state == "Skipped"]
    handled = [item for item in previews if item.state == "Handled"]
    metrics = st.columns(4)
    metrics[0].metric("Due", len(due))
    metrics[1].metric("Blocked", len(blocked))
    metrics[2].metric("Skipped", len(skipped))
    metrics[3].metric("Generated", len(handled))

    selected = []
    if not due:
        st.info("No recurring entries are due through this date.")
    for item in due:
        item_key = f"{item.schedule_id}_{item.period_start.isoformat()}"
        key = _key(f"recurring_select_{item_key}")
        cols = st.columns([0.4, 2.2, 1.3, 1.2])
        with cols[0]:
            chosen = st.checkbox("Select", key=key, label_visibility="collapsed")
        with cols[1]:
            st.markdown(f"**{item.template_name}**  \n{item.period_name}")
        with cols[2]:
            st.caption(f"Entry date  \n{display_date(item.entry_date, date_format)}")
        with cols[3]:
            with st.popover("Skip"):
                reason = st.text_input(
                    "Reason", key=_key(f"recurring_skip_reason_{item_key}"),
                    placeholder="Why it does not apply",
                )
                if st.button(
                    "Skip this period", key=_key(f"recurring_skip_{item_key}"),
                    disabled=dbconn.READ_ONLY or not reason.strip(),
                ):
                    try:
                        skip_occurrence(
                            client.id, item.schedule_id, item.period_start,
                            item.period_end, reason,
                        )
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.recurring_result = (
                            f"Skipped {item.template_name} for {item.period_name}."
                        )
                        st.rerun()
        if chosen:
            selected.append((item.schedule_id, item.period_start, item.period_end))
    if due and st.button(
        "Generate selected drafts", type="primary",
        disabled=dbconn.READ_ONLY or not selected,
        key=_key("recurring_generate_selected"),
    ):
        result = generate_selected(client.id, selected)
        st.session_state.recurring_result = (
            f"Generated {len(result['generated'])}; already generated "
            f"{len(result['already_generated'])}; skipped {len(result['skipped'])}; "
            f"errors {len(result['errors'])}. "
            f"Accounted for {result['accounted_count']} of "
            f"{result['requested_count']} selected occurrences."
        )
        if result["errors"]:
            st.session_state.recurring_errors = result["errors"]
        st.rerun()

    if blocked:
        st.markdown("**Action needed**")
        missing_years = set()
        for item in blocked:
            st.warning(f"{item.template_name} · {item.period_name}: {item.reason}")
            if "fiscal calendar is missing" in item.reason:
                missing_years.add(
                    fiscal_year_ending_year(
                        item.entry_date, client.fiscal_year_end_month
                    )
                )
        for year in sorted(missing_years):
            if st.button(
                f"Create FY {year} fiscal calendar",
                key=_key(f"recurring_create_calendar_{year}"),
                disabled=dbconn.READ_ONLY,
            ):
                try:
                    FiscalPeriod.ensure_periods_exist(
                        client.id, year, client.fiscal_year_end_month
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state.recurring_result = f"FY {year} calendar created."
                    st.rerun()

    if skipped:
        with st.expander(f"Skipped periods ({len(skipped)})"):
            for item in skipped:
                left, right = st.columns([4, 1])
                left.caption(
                    f"{item.template_name} · {item.period_name} · {item.reason}"
                )
                with right:
                    if st.button(
                        "Undo skip", key=_key(f"recurring_undo_skip_{item.occurrence_id}"),
                        disabled=dbconn.READ_ONLY,
                    ):
                        try:
                            undo_skip(client.id, item.occurrence_id)
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.recurring_result = "Skip undone."
                            st.rerun()

    rejected = rejected_recoveries(client.id)
    if rejected:
        with st.expander(f"Rejected drafts available to regenerate ({len(rejected)})"):
            for item in rejected:
                role = item["role"]
                left, right = st.columns([4, 1])
                left.caption(
                    f"{item['template_name']} · {item['period_name']} · "
                    f"{role.lower()} draft #{item['draft_id']} rejected"
                )
                with right:
                    if st.button(
                        f"Regenerate {role.lower()}",
                        key=_key(
                            f"recurring_regenerate_{role.lower()}_"
                            f"{item['occurrence_id']}"
                        ),
                        disabled=dbconn.READ_ONLY,
                    ):
                        try:
                            regenerate_occurrence(
                                client.id, item["occurrence_id"], role=role
                            )
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.recurring_result = (
                                f"Created a replacement {role.lower()} draft for "
                                f"{item['template_name']}."
                            )
                            st.rerun()


def _render_templates(client, date_format: str, load_template) -> None:
    st.subheader("Templates")
    accounts = Account.get_all(client.id, active_only=False)
    active = JournalEntryTemplate.get_all(client.id)
    all_templates = JournalEntryTemplate.get_all(client.id, include_archived=True)
    archived = [template for template in all_templates if template.archived_at]
    if st.button(
        "Create template", key=_key("recurring_create_template"),
        disabled=dbconn.READ_ONLY,
    ):
        _open_template_editor(None, accounts)
        st.rerun()
    _render_template_editor(client.id, accounts)

    if not active:
        st.info("No active journal-entry templates yet.")
    for template in active:
        schedule = RecurringSchedule.get_for_template(template.id, client.id)
        with st.container(border=True):
            title_col, status_col = st.columns([3, 1])
            title_col.markdown(f"**{template.name}**  \n{template.description}")
            status_col.caption(
                "Unscheduled" if not schedule
                else (f"{schedule.frequency} · "
                      f"{'Active' if schedule.is_active else 'Paused'}")
            )
            st.dataframe([
                {
                    "Account": next(
                        (account.display_name() for account in accounts
                         if account.id == line.account_id), "Unavailable"
                    ),
                    "Debit": line.debit_cents / 100 if line.debit_cents else None,
                    "Credit": line.credit_cents / 100 if line.credit_cents else None,
                    "Memo": line.memo or "",
                }
                for line in template.lines
            ], hide_index=True, width="stretch")
            use_col, edit_col, archive_col, _ = st.columns([1, 1, 1, 2])
            with use_col:
                if st.button("Use", key=_key(f"recurring_use_{template.id}")):
                    load_template(template)
                    st.rerun()
            with edit_col:
                if st.button(
                    "Edit", key=_key(f"recurring_edit_{template.id}"),
                    disabled=dbconn.READ_ONLY,
                ):
                    _open_template_editor(template, accounts)
                    st.rerun()
            with archive_col:
                if st.session_state.get("recurring_confirm_archive") == template.id:
                    if st.button(
                        "Confirm archive", key=_key(f"recurring_archive_confirm_{template.id}"),
                        disabled=dbconn.READ_ONLY,
                    ):
                        try:
                            template.archive()
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.pop("recurring_confirm_archive", None)
                            st.session_state.recurring_result = (
                                f"Template {template.name} archived."
                            )
                            st.rerun()
                elif st.button(
                    "Archive", key=_key(f"recurring_archive_{template.id}"),
                    disabled=dbconn.READ_ONLY,
                ):
                    st.session_state.recurring_confirm_archive = template.id
                    st.rerun()
            _render_schedule_editor(
                client.id, template, schedule, date_format
            )

    if archived:
        with st.expander(f"Archived templates ({len(archived)})"):
            for template in archived:
                left, right = st.columns([4, 1])
                left.caption(f"{template.name} · archived by {template.archived_by}")
                with right:
                    if st.button(
                        "Restore", key=_key(f"recurring_restore_{template.id}"),
                        disabled=dbconn.READ_ONLY,
                    ):
                        try:
                            template.restore()
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.recurring_result = (
                                f"Template {template.name} restored. Its schedule remains paused."
                            )
                            st.rerun()


def _render_history(client_id: int) -> None:
    history = occurrence_history(client_id)
    if not history:
        return
    with st.expander(f"Generation history ({len(history)})"):
        rows = []
        for occurrence in history:
            if not occurrence["drafts"]:
                rows.append({
                    "Template": occurrence["template_name"],
                    "Period": occurrence["period_name"],
                    "Role": "—", "Generation": "—", "Draft": "—",
                    "Result": "Skipped", "Journal entry": "—",
                })
            for draft in occurrence["drafts"]:
                rows.append({
                    "Template": occurrence["template_name"],
                    "Period": occurrence["period_name"],
                    "Role": draft["role"],
                    "Generation": draft["generation_number"],
                    "Draft": f"#{draft['draft_id']}",
                    "Result": draft["draft_status"].title(),
                    "Journal entry": (
                        f"#{draft['posted_entry_id']}"
                        if draft["posted_entry_id"] else "—"
                    ),
                })
        st.dataframe(rows, hide_index=True, width="stretch")


def render_recurring_view(client, date_format: str, load_template) -> None:
    st.subheader("Templates & recurring")
    st.caption(
        "Reuse balanced entries or generate scheduled drafts. Nothing posts "
        "until you approve it in Drafts."
    )
    if dbconn.READ_ONLY:
        st.info("This book is read-only. Templates and schedules can be viewed but not changed.")
    message = st.session_state.pop("recurring_result", None)
    if message:
        st.success(message)
    errors = st.session_state.pop("recurring_errors", None)
    if errors:
        for error in errors:
            st.error(
                f"Schedule {error['schedule_id']} · {error['period_start']}: "
                f"{error['error']}"
            )
    _render_due(client, date_format)
    st.divider()
    _render_templates(client, date_format, load_template)
    _render_history(client.id)
