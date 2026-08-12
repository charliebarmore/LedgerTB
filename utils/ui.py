"""Shared UI building blocks."""

import streamlit as st

# A horizontal radio styled as segmented tabs (dots hidden, underlined
# selection). Scoped to one widget via its .st-key-* container class so
# genuine radio inputs elsewhere on the page keep their dots.
_SWITCHER_CSS = """
<style>
.st-key-{wkey} div[role="radiogroup"] {{
    gap: 0;
    border-bottom: 1px solid #d8dee8;
}}
.st-key-{wkey} label[data-testid="stRadioOption"] {{
    padding: 0.25rem 1rem 0.4rem 0.75rem;
    margin-right: 0;
    border-bottom: 2px solid transparent;
}}
.st-key-{wkey} label[data-testid="stRadioOption"][data-selected="true"] {{
    border-bottom-color: #1f3a5f;
    font-weight: 600;
}}
.st-key-{wkey} label[data-testid="stRadioOption"] > div > div > div:first-child {{
    display: none;  /* hide the radio dot */
}}
</style>
"""


_MISSING = object()


def apply_default_on_change(widget_key, depends_on, default_value):
    """Re-apply a keyed widget's default when what it derives from changes.

    A keyed Streamlit widget ignores ``index=``/``value=`` once its key exists
    in session state. A default computed from another control — a sign
    convention derived from the selected account, say — therefore lands on the
    first render and never again, so changing the other control silently leaves
    the stale value in place.

    Re-applying on every run would be just as wrong: it would overwrite a
    deliberate override the moment anything else on the page rerun. So the
    dependency is tracked and the default re-applied only when it actually
    changed, leaving an override intact until the user picks a different
    account.

    Must be called BEFORE the widget renders — that is the only point at which
    a keyed widget's session value may legally be overwritten.
    """
    tracker_key = f"_{widget_key}_depends_on"
    # A sentinel, not None: None is a legitimate dependency value (no account
    # selected yet) and must not read as "never seen".
    if st.session_state.get(tracker_key, _MISSING) != depends_on:
        st.session_state[tracker_key] = depends_on
        st.session_state[widget_key] = default_value


def view_switcher(options, key, label="View"):
    """Tab-styled switcher for a page's views. st.tabs can't be preselected,
    so pages that need deep links (sidebar buttons, post-action jumps) use
    this instead.

    The selection lives in st.session_state[key], which stays a PLAIN session
    var: any code may write it at any time (before or after this renders) and
    the switcher picks it up on the next run. The radio itself is bound to a
    private widget key that is only synced from st.session_state[key] when
    that value changed programmatically since the last render — syncing
    unconditionally would undo the user's own click, which reaches the script
    one run later via the widget state.
    """
    widget_key = f"_{key}_widget"
    shadow_key = f"_{key}_rendered"

    current = st.session_state.get(key, options[0])
    if current not in options:
        current = options[0]
    if st.session_state.get(shadow_key) != current:
        st.session_state[widget_key] = current

    # ``st.html`` treats this as styling rather than Markdown content, keeping
    # the raw CSS out of screen-reader and browser accessibility trees.
    st.html(_SWITCHER_CSS.format(wkey=widget_key))
    selected = st.radio(label, options, key=widget_key,
                        horizontal=True, label_visibility="collapsed")

    st.session_state[key] = selected
    st.session_state[shadow_key] = selected
    return selected


_PARKING_HINTS = ("ask my accountant", "uncategorized", "suspense")


def is_parking_account(label):
    """Whether an account label is a park-it-for-review bucket rather than a
    real category — "Ask My Accountant", "Uncategorized", "Suspense". A row
    coded to one is filed, not decided, so review screens flag it instead of
    letting it read as categorized."""
    return any(hint in (label or "").lower() for hint in _PARKING_HINTS)


_STATEMENT_CSS = """
<style>
table.pb-statement {
    width: 100%;
    max-width: 44rem;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
    margin: 0.25rem 0 0.75rem 0;
}
table.pb-statement.wide { max-width: 64rem; }
table.pb-statement td {
    border: none;
    padding: 0.16rem 0.25rem;
    vertical-align: bottom;
}
table.pb-statement td.amt { text-align: right; white-space: nowrap; width: 8.5rem; }
table.pb-statement td.note-cell { color: #6b7280; font-size: 0.85em; }
table.pb-statement span.muted { color: #6b7280; font-size: 0.85em; margin-left: 0.5rem; }
table.pb-statement tr.head td {
    font-weight: 600; color: #6b7280; font-size: 0.8em;
    text-transform: uppercase; letter-spacing: 0.04em;
    border-bottom: 1px solid #b9bec7;
}
table.pb-statement tr.section td {
    font-weight: 700; font-size: 1.02em; padding-top: 0.9rem;
}
table.pb-statement tr.item td.lbl { padding-left: 1.3rem; }
table.pb-statement tr.subtotal td {
    font-weight: 700; border-top: 1px solid #565d68; padding-bottom: 0.5rem;
}
table.pb-statement tr.total td {
    font-weight: 700; font-size: 1.02em; border-top: 1px solid #565d68;
}
table.pb-statement tr.total td.amt { border-bottom: 3px double #565d68; }
</style>
"""


def _statement_amount(value, lead_dollar, value_format="money"):
    if value is None:
        return ""
    if value_format == "percent":
        body = f"{abs(value):,.1f}%"
        return f"({body})" if value < 0 else body
    body = f"{abs(value):,.2f}"
    if value < 0:
        body = f"({body})"
    return f"${body}" if lead_dollar else body


def financial_statement(rows, headers=None, formats=None):
    """Render rows as an actual financial statement, not a widget pile.

    rows: iterables of (kind, label, amounts, note) — note optional.
      kind: 'section' (bold heading, no amounts), 'item' (indented line),
            'subtotal' (bold, top rule), 'total' (bold, double-ruled amount),
            'note' (muted caption line).
      amounts: list of floats/None, one per amount column (usually one;
               two for debit/credit layouts). Dollar signs appear on
               subtotal/total rows, accounting-style; negatives in parens.
    headers: optional list of amount-column headings.
    formats: optional per-column formats ("money" or "percent").
    """
    import html as _html

    columns = max((len(r[2]) for r in rows if len(r) > 2 and r[2]), default=1)
    column_formats = list(formats or []) + ["money"] * columns
    parts = []
    if headers:
        cells = "".join(f"<td class='amt'>{_html.escape(h)}</td>" for h in headers)
        parts.append(f"<tr class='head'><td class='lbl'></td>{cells}</tr>")
    for row in rows:
        kind, label = row[0], row[1]
        amounts = row[2] if len(row) > 2 and row[2] is not None else []
        note = row[3] if len(row) > 3 else None
        label_html = _html.escape(str(label))
        if note:
            label_html += f"<span class='muted'>{_html.escape(str(note))}</span>"
        if kind == "note":
            parts.append(
                f"<tr class='note'><td class='lbl note-cell' colspan='{columns + 1}'>"
                f"{label_html}</td></tr>"
            )
            continue
        lead = kind in ("subtotal", "total")
        padded = list(amounts) + [None] * (columns - len(amounts))
        cells = "".join(
            f"<td class='amt'>{_statement_amount(a, lead, column_formats[index])}</td>"
            for index, a in enumerate(padded)
        )
        parts.append(f"<tr class='{kind}'><td class='lbl'>{label_html}</td>{cells}</tr>")

    table_class = "pb-statement wide" if columns >= 4 else "pb-statement"
    st.html(
        _STATEMENT_CSS
        + f"<table class='{table_class}'>{''.join(parts)}</table>"
    )


_LEDGER_CSS = """
<style>
table.pb-ledger {
    width: 100%;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
    font-size: 0.92em;
    margin: 0.25rem 0 0.75rem 0;
}
table.pb-ledger td, table.pb-ledger th {
    border: none;
    padding: 0.22rem 0.5rem 0.22rem 0.25rem;
    vertical-align: top;
    text-align: left;
}
table.pb-ledger th {
    font-weight: 600; color: #6b7280; font-size: 0.85em;
    text-transform: uppercase; letter-spacing: 0.04em;
    border-bottom: 1px solid #b9bec7;
}
table.pb-ledger td.r, table.pb-ledger th.r { text-align: right; white-space: nowrap; }
table.pb-ledger tr:nth-child(even) td { background: rgba(151, 166, 195, 0.08); }
table.pb-ledger tr.total td {
    font-weight: 700; border-top: 1px solid #565d68; background: none;
}
</style>
"""


def ledger_table(headers, rows, align, total_row=None):
    """A clean listing table for ledger-style reports.

    headers: column headings; rows: lists of display strings;
    align: 'l'/'r' per column; total_row: optional bold ruled last row.
    """
    import html as _html

    def cells(values, tag):
        return "".join(
            f"<{tag} class='{'r' if a == 'r' else ''}'>{_html.escape(str(v))}</{tag}>"
            for v, a in zip(values, align)
        )

    body = [f"<tr>{cells(headers, 'th')}</tr>"]
    body += [f"<tr>{cells(row, 'td')}</tr>" for row in rows]
    if total_row is not None:
        body.append(f"<tr class='total'>{cells(total_row, 'td')}</tr>")
    st.html(_LEDGER_CSS + f"<table class='pb-ledger'>{''.join(body)}</table>")
