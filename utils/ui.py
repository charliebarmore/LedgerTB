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

    st.markdown(_SWITCHER_CSS.format(wkey=widget_key), unsafe_allow_html=True)
    selected = st.radio(label, options, key=widget_key,
                        horizontal=True, label_visibility="collapsed")

    st.session_state[key] = selected
    st.session_state[shadow_key] = selected
    return selected
