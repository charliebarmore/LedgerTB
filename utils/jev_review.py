"""Small optional suggestion panel embedded in the existing import review page."""
import streamlit as st

from services import jev_categorization as jev
from utils import secure_store
from utils.import_review import row_key


def _accept_suggestion(transaction, account_id, request_key):
    # Callbacks run before the page's summary and stale-input reconciliation.
    # This human category choice must be reflected in the same rendered frame.
    st.session_state[row_key("cat", transaction)] = account_id
    transaction["selected_account_id"] = account_id
    transaction.pop("ai_review_accepted", None)
    transaction["jev_accepted"] = {"key": request_key, "account_id": account_id}


def prepare_jev_review(transactions, accounts, client_id, book, business_context, session_state,
                       *, include_unaccepted=True):
    """Reconcile accepted categories before posting, even when Jev is now off.

    Widget state disappears when a view unmounts. The row retains the human's
    selection, including an explicit clear, and is the fallback on remount.
    Only chosen, previously requested, or accepted rows need cloud input hashes.
    Loading a large import must not duplicate the whole chart for every row.
    """
    scope = (str(book), client_id)
    inputs, keys = {}, {}
    known = set(session_state.get("jev_known_rows", ()))
    chosen = session_state.get("bulk_rows", session_state.get("jev_rows", ()))
    relevant = known | (set(chosen) if len(chosen) <= jev.MAX_BATCH else set())
    if include_unaccepted:
        session_state["jev_known_rows"] = sorted(known & {t["uid"] for t in transactions})
    for transaction in transactions:
        if not transaction.get("jev_accepted") and (
            not include_unaccepted or transaction["uid"] not in relevant
        ):
            continue
        uid = transaction["uid"]
        evidence = {
            **transaction,
            "is_transfer": session_state.get(
                row_key("xfer", transaction), transaction.get("is_transfer", False)
            ),
        }
        inputs[uid] = jev.request_input(evidence, accounts, client_id, business_context)
        keys[uid] = jev.request_key(scope, inputs[uid])
        accepted = transaction.get("jev_accepted")
        if not accepted:
            continue
        cat_key = row_key("cat", transaction)
        current = session_state.get(cat_key, transaction.get("selected_account_id"))
        if current != accepted["account_id"]:
            # A later human choice supersedes Jev and must not be cleared.
            transaction.pop("jev_accepted", None)
        elif accepted["key"] != keys[uid]:
            session_state[cat_key] = None
            transaction["selected_account_id"] = 0
            transaction.pop("jev_accepted", None)
    return inputs, keys


def render_jev_review(transactions, accounts, client_id, prepared, *, chosen=None, show_results=True):
    with st.expander("What is sent to TypeSafe"):
        st.caption(
            "TypeSafe Jev sends only the rows you choose: dates, descriptions, amounts, "
            "source account IDs, transfer flags and receipt text if present, plus eligible "
            "account IDs/names/numbers/types and the client's AI business context. "
            "General client Notes are not sent. Every suggestion needs your review. "
            "Choosing rows here does not change inclusion for posting."
        )
    st.caption("Suggestions only. Every account choice needs your approval.")
    inputs, keys = prepared
    cache = st.session_state.setdefault("jev_results", {})
    by_uid = {t["uid"]: t for t in transactions}
    if chosen is None:
        st.session_state["jev_rows"] = [uid for uid in st.session_state.get("jev_rows", []) if uid in by_uid]
        chosen = st.multiselect(
            "Rows to ask Jev about", options=list(by_uid), key="jev_rows", max_selections=jev.MAX_BATCH,
            format_func=lambda uid: f"{by_uid[uid]['date']} | {by_uid[uid]['description']} | ${by_uid[uid]['amount']:,.2f}",
        )
    if len(chosen) > jev.MAX_BATCH:
        st.info(f"Select at most {jev.MAX_BATCH} rows for a Jev request. Your selection and posting inclusion are unchanged.")
        chosen = []
    st.caption(f"{len(chosen)} rows selected for suggestions.")
    consent = st.checkbox("Send the selected transaction information to TypeSafe", key="jev_consent")
    api_key = secure_store.get_secret("typesafe_api_key")
    if not api_key:
        st.info("Add your TypeSafe API key in Firm Settings. Local review remains available.")
    requested = {keys[uid]: inputs[uid] for uid in chosen}
    new_requests = jev.plan_requests({k: v for k, v in requested.items() if k not in cache})
    if new_requests:
        count = sum(fits for _, fits in new_requests)
        st.caption(f"This selection needs {count} new TypeSafe request(s). Existing results are reused.")
        if any(not fits for _, fits in new_requests):
            st.info("Some selected evidence is too long for Jev and will remain for local review.")
    run = st.button("Ask Jev for suggestions", disabled=not (chosen and consent and api_key), key="jev_run")
    has_error = any(cache.get(k, {}).get("error") and cache[k].get("retryable", True) for k in requested)
    retry = st.button("Retry failed Jev requests", disabled=not (has_error and consent and api_key), key="jev_retry",
                      help="Wait after a rate limit. A retry may incur another charge, including after a timeout.")
    if run or retry:
        st.session_state["jev_known_rows"] = sorted(set(st.session_state.get("jev_known_rows", ())) | set(chosen))
        with st.spinner("Jev is reviewing the selected information..."):
            jev.suggest(requested, cache, api_key=api_key, consent=consent, retry=retry)
        # Refresh result-dependent controls immediately. The cached failure or
        # success prevents this rerun from making another paid request.
        st.rerun()
    if show_results:
        for transaction in transactions:
            render_jev_result(transaction, accounts, client_id, prepared)


def render_jev_result(transaction, accounts, client_id, prepared):
    """Render a cached suggestion beside its row; never initiates a request."""
    _, keys = prepared
    uid = transaction["uid"]
    result = st.session_state.get("jev_results", {}).get(keys.get(uid))
    if not result:
        return
    st.text(f"TypeSafe Jev · {result.get("model", jev.MODEL)}")
    if result.get("error"):
        st.warning(result["error"] + " Your staged transactions are unchanged.")
        return
    account_names = {a.id: a.display_name() for a in jev.eligible_accounts(accounts, client_id)}
    if result["outcome"] == "account":
        account_id = result["account_id"]
        st.caption(f"Suggested account: {account_names[account_id]}")
        if st.button("Accept account suggestion", key=f"jev_accept_{uid}_{keys[uid]}",
                     on_click=_accept_suggestion, args=(transaction, account_id, keys[uid])):
            st.success("Account accepted. Inclusion for posting is unchanged.")
    else:
        st.info(jev.OUTCOMES[result["outcome"]])
    st.caption(f"Distribution concentration: {result['confidence']:.0%}. Not a guarantee of correctness.")
