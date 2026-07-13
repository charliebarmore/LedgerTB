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
