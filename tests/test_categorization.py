from types import SimpleNamespace
from unittest.mock import MagicMock

from models.account import Account
from services.categorization import CategorizationService


def make_service_with_mocked_response(tool_input):
    """Build a CategorizationService whose Anthropic client returns a
    canned tool_use response, without hitting the real API."""
    service = CategorizationService()
    fake_tool_use_block = SimpleNamespace(type="tool_use", input=tool_input)
    fake_response = SimpleNamespace(content=[fake_tool_use_block])
    service.client = MagicMock()
    service.client.messages.create.return_value = fake_response
    return service


def test_categorize_transactions_applies_tool_use_suggestions():
    accounts = [
        Account(id=1, client_id=1, account_number="6300", name="Software", type="Expense", is_active=True),
        Account(id=2, client_id=1, account_number="4000", name="Fees", type="Revenue", is_active=True),
    ]
    transactions = [
        {"date": "2026-01-15", "description": "GITHUB SUBSCRIPTION", "amount": -25.0},
        {"date": "2026-01-16", "description": "CLIENT PAYMENT", "amount": 1500.0},
    ]
    tool_input = {
        "suggestions": [
            {"index": 1, "account_number": "6300", "confidence": "high", "reason": "Software subscription"},
            {"index": 2, "account_number": "4000", "confidence": "high", "reason": "Client payment"},
        ]
    }
    service = make_service_with_mocked_response(tool_input)

    result = service.categorize_transactions(transactions, accounts)

    assert service.last_error is None
    assert service.last_matched == 2
    assert result[0]["suggested_account_id"] == 1
    assert result[0]["confidence"] == "high"
    assert result[1]["suggested_account_id"] == 2


def test_categorize_transactions_handles_unmatched_account_number():
    accounts = [Account(id=1, client_id=1, account_number="6300", name="Software", type="Expense", is_active=True)]
    transactions = [{"date": "2026-01-15", "description": "MYSTERY CHARGE", "amount": -10.0}]
    tool_input = {
        "suggestions": [
            {"index": 1, "account_number": "9999", "confidence": "low", "reason": "Unrecognized account"},
        ]
    }
    service = make_service_with_mocked_response(tool_input)

    result = service.categorize_transactions(transactions, accounts)

    assert service.last_matched == 0
    assert "suggested_account_id" not in result[0]


def test_categorize_transactions_missing_tool_call_sets_error():
    accounts = [Account(id=1, client_id=1, account_number="6300", name="Software", type="Expense", is_active=True)]
    transactions = [{"date": "2026-01-15", "description": "SOMETHING", "amount": -10.0}]

    service = CategorizationService()
    service.client = MagicMock()
    service.client.messages.create.return_value = SimpleNamespace(content=[])  # no tool_use block

    result = service.categorize_transactions(transactions, accounts)

    assert service.last_error is not None
    assert "suggested_account_id" not in result[0]


def test_categorize_transactions_batches_and_aggregates_stats():
    accounts = [Account(id=1, client_id=1, account_number="6300", name="Software", type="Expense", is_active=True)]
    transactions = [
        {"date": "2026-01-01", "description": f"TXN {i}", "amount": -1.0}
        for i in range(3)
    ]

    service = CategorizationService()
    service.client = MagicMock()

    def make_response(*args, **kwargs):
        # Each batch call gets one suggestion for its single transaction.
        return SimpleNamespace(content=[SimpleNamespace(
            type="tool_use",
            input={"suggestions": [{"index": 1, "account_number": "6300", "confidence": "high", "reason": "x"}]}
        )])

    service.client.messages.create.side_effect = make_response

    result = service.categorize_transactions(transactions, accounts, batch_size=1)

    assert service.last_matched == 3
    assert service.last_total == 3
    assert all("suggested_account_id" in t for t in result)
