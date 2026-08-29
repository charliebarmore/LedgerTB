from utils.client_context import (
    client_context_identity,
    pop_client_intent,
    scope_page_to_client,
    set_client_intent,
    sync_active_client_context,
)


def test_client_context_identity_includes_resolved_book_and_client(tmp_path):
    book = tmp_path / "folder" / ".." / "book.db"

    assert client_context_identity(book, 7) == (
        str((tmp_path / "book.db").resolve()),
        7,
    )


def test_active_context_changes_for_client_or_book(tmp_path):
    state = {}
    first_book = tmp_path / "first.db"
    second_book = tmp_path / "second.db"

    assert sync_active_client_context(state, 1, first_book) is False
    assert sync_active_client_context(state, 1, first_book) is False
    assert sync_active_client_context(state, 2, first_book) is True
    # Client ids restart in each book; the path must still trigger a change.
    assert sync_active_client_context(state, 2, second_book) is True
    assert state["_active_client_context_generation"] == 2


def test_page_scope_rotates_every_time_ownership_changes(tmp_path):
    state = {}
    first_book = tmp_path / "first.db"
    second_book = tmp_path / "second.db"

    first = scope_page_to_client(state, "journal_entries", 1, first_book)
    stable = scope_page_to_client(state, "journal_entries", 1, first_book)
    other_book = scope_page_to_client(state, "journal_entries", 1, second_book)
    returned = scope_page_to_client(state, "journal_entries", 1, first_book)

    assert (first.generation, first.changed) == (0, False)
    assert (stable.generation, stable.changed) == (0, False)
    assert (other_book.generation, other_book.changed) == (1, True)
    assert (returned.generation, returned.changed) == (2, True)
    assert returned.key("save") == "save__journal_entries_g2"


def test_client_intent_is_one_shot_for_its_owner(tmp_path):
    state = {}
    book = tmp_path / "book.db"
    value = {"entry_id": 42, "view": "New Entry"}

    set_client_intent(state, "journal", value, 7, book)

    assert pop_client_intent(state, "journal", 7, book) == value
    assert pop_client_intent(state, "journal", 7, book) is None


def test_client_intent_rejects_other_clients_and_same_id_in_other_book(tmp_path):
    state = {}
    first_book = tmp_path / "first.db"
    second_book = tmp_path / "second.db"

    set_client_intent(state, "journal", {"entry_id": 11}, 1, first_book)
    assert pop_client_intent(state, "journal", 2, first_book) is None

    set_client_intent(state, "journal", {"entry_id": 11}, 1, first_book)
    assert pop_client_intent(state, "journal", 1, second_book) is None


def test_book_scoped_key_tracks_the_book(tmp_path):
    from utils.client_context import book_scoped_key

    first = tmp_path / "first.db"
    second = tmp_path / "second.db"

    assert book_scoped_key("firm_date_format", first) == \
        book_scoped_key("firm_date_format", first)
    assert book_scoped_key("firm_date_format", first) != \
        book_scoped_key("firm_date_format", second)
    # Path spelling must not matter — the same book is the same key.
    assert book_scoped_key("firm_date_format", first) == \
        book_scoped_key("firm_date_format", tmp_path / "sub" / ".." / "first.db")
    assert book_scoped_key("firm_date_format", first).startswith("firm_date_format_")
