"""Domain constants: the single source of truth for account types, journal
entry types, and imported-transaction statuses. Reference these instead of bare
string literals so a typo becomes an ImportError rather than a silent
misclassification, and so the option lists live in one place.
"""


class AccountType:
    ASSET = "Asset"
    LIABILITY = "Liability"
    EQUITY = "Equity"
    REVENUE = "Revenue"
    EXPENSE = "Expense"

    # Ordered for UI dropdowns (statement order: BS accounts then P&L).
    ALL = [ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE]

    # Display labels cannot be formed by blindly appending "s": liability and
    # equity both change spelling in the plural.
    PLURAL_LABELS = {
        ASSET: "Assets",
        LIABILITY: "Liabilities",
        EQUITY: "Equities",
        REVENUE: "Revenues",
        EXPENSE: "Expenses",
    }

    # Accounts whose normal balance is a debit (assets, expenses); everything
    # else (liabilities, equity, revenue) is credit-normal.
    DEBIT_NORMAL = (ASSET, EXPENSE)

    @staticmethod
    def is_debit_normal(account_type: str) -> bool:
        return account_type in AccountType.DEBIT_NORMAL

    @staticmethod
    def plural_label(account_type: str) -> str:
        return AccountType.PLURAL_LABELS.get(account_type, f"{account_type}s")


class EntryType:
    REGULAR = "Regular"
    ADJUSTING = "Adjusting"
    CLOSING = "Closing"
    BEGINNING_BALANCE = "Beginning Balance"

    ALL = [REGULAR, ADJUSTING, CLOSING, BEGINNING_BALANCE]


class TxnStatus:
    PENDING = "Pending"
    CATEGORIZED = "Categorized"
    POSTED = "Posted"
    DISMISSED = "Dismissed"


# Fallback accounts the AI categorizer suggests when nothing else fits. These
# account numbers must exist in the seeded chart of accounts
# (see database/seed_data.py); if a client's custom chart omits them, the
# suggestion simply won't match (handled gracefully) rather than breaking.
DEFAULT_MISC_EXPENSE_ACCOUNT = "7500"   # Miscellaneous Expense
DEFAULT_OTHER_INCOME_ACCOUNT = "4900"   # Other Income
