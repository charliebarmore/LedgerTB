"""Tests for the get_cursor() context manager (M6 connection-leak fix)."""

import pytest

from database.connection import get_cursor
from models.client import Client


def _client_count():
    with get_cursor() as cursor:
        return cursor.execute("SELECT COUNT(*) FROM clients").fetchone()[0]


def test_get_cursor_commits_on_success(db):
    with get_cursor(commit=True) as cursor:
        cursor.execute(
            "INSERT INTO clients (name, entity_type, fiscal_year_end_month, is_active) "
            "VALUES ('Committed Co', 'S-Corp', 12, 1)"
        )
    assert any(c.name == "Committed Co" for c in Client.get_all())


def test_get_cursor_rolls_back_on_error(db):
    before = _client_count()
    with pytest.raises(RuntimeError):
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "INSERT INTO clients (name, entity_type, fiscal_year_end_month, is_active) "
                "VALUES ('Doomed Co', 'S-Corp', 12, 1)"
            )
            raise RuntimeError("boom before commit")
    # The insert must have been rolled back, not committed.
    assert _client_count() == before
    assert not any(c.name == "Doomed Co" for c in Client.get_all())


def test_get_cursor_closes_connection_even_on_error(db):
    """After an error inside get_cursor, the connection is released, so
    subsequent writes still work (a leaked/locked connection would block them)."""
    with pytest.raises(RuntimeError):
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "INSERT INTO clients (name, entity_type, fiscal_year_end_month, is_active) "
                "VALUES ('Temp', 'S-Corp', 12, 1)"
            )
            raise RuntimeError("boom")

    # This write would hang/fail on 'database is locked' if the prior connection leaked.
    Client(name="After Error Co", entity_type="S-Corp", fiscal_year_end_month=12).save(seed_accounts=False)
    assert any(c.name == "After Error Co" for c in Client.get_all())
