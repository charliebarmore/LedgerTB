"""Domain constants: the single source of truth for account types, journal
entry types, and imported-transaction statuses. Reference these instead of bare
string literals so a typo becomes an ImportError rather than a silent
misclassification, and so the option lists live in one place.
"""

import re


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


class AccountSubtype:
    """Curated financial-statement classifications for chart accounts.

    Stored subtype text predates this vocabulary, so legacy values remain
    readable. ``resolve`` translates only known, accounting-safe aliases for
    reporting and review suggestions; ``is_canonical`` deliberately remains
    strict so the Chart of Accounts can show what still needs human review.
    """

    CASH = "Cash"
    ACCOUNTS_RECEIVABLE = "Accounts Receivable"
    INVENTORY = "Inventory"
    OTHER_CURRENT_ASSET = "Other Current Asset"
    FIXED_ASSET = "Fixed Asset"
    ACCUMULATED_DEPRECIATION = "Accumulated Depreciation"
    OTHER_ASSET = "Other Asset"

    ACCOUNTS_PAYABLE = "Accounts Payable"
    CREDIT_CARD = "Credit Card"
    OTHER_CURRENT_LIABILITY = "Other Current Liability"
    SHORT_TERM_DEBT = "Short-Term Debt"
    LONG_TERM_LIABILITY = "Long-Term Liability"

    OWNER_CONTRIBUTION = "Owner Contribution"
    OWNER_DISTRIBUTION = "Owner Distribution"
    RETAINED_EARNINGS = "Retained Earnings"
    OTHER_EQUITY = "Other Equity"

    OPERATING_REVENUE = "Operating Revenue"
    OTHER_INCOME = "Other Income"
    GAIN_ON_ASSET_DISPOSAL = "Gain on Asset Disposal"

    COST_OF_GOODS_SOLD = "Cost of Goods Sold"
    OPERATING_EXPENSE = "Operating Expense"
    DEPRECIATION_AMORTIZATION = "Depreciation & Amortization"
    OTHER_EXPENSE = "Other Expense"
    LOSS_ON_ASSET_DISPOSAL = "Loss on Asset Disposal"

    # The list order is statement display order and must stay stable.
    BY_TYPE = {
        AccountType.ASSET: [
            CASH, ACCOUNTS_RECEIVABLE, INVENTORY, OTHER_CURRENT_ASSET,
            FIXED_ASSET, ACCUMULATED_DEPRECIATION, OTHER_ASSET,
        ],
        AccountType.LIABILITY: [
            ACCOUNTS_PAYABLE, CREDIT_CARD, OTHER_CURRENT_LIABILITY,
            SHORT_TERM_DEBT, LONG_TERM_LIABILITY,
        ],
        AccountType.EQUITY: [
            OWNER_CONTRIBUTION, OWNER_DISTRIBUTION, RETAINED_EARNINGS,
            OTHER_EQUITY,
        ],
        AccountType.REVENUE: [
            OPERATING_REVENUE, OTHER_INCOME, GAIN_ON_ASSET_DISPOSAL,
        ],
        AccountType.EXPENSE: [
            COST_OF_GOODS_SOLD, OPERATING_EXPENSE,
            DEPRECIATION_AMORTIZATION, OTHER_EXPENSE,
            LOSS_ON_ASSET_DISPOSAL,
        ],
    }

    # Presentation groupings sit above individual subtypes. A group may carry
    # more than one subtype where the useful financial-statement subtotal is a
    # net or combined amount (for example fixed assets less accumulated
    # depreciation).
    STATEMENT_GROUPS = {
        AccountType.ASSET: [
            (
                "current_assets", "Current Assets",
                (CASH, ACCOUNTS_RECEIVABLE, INVENTORY, OTHER_CURRENT_ASSET),
            ),
            (
                "property_equipment_net", "Property and Equipment, Net",
                (FIXED_ASSET, ACCUMULATED_DEPRECIATION),
            ),
            ("other_assets", "Other Assets", (OTHER_ASSET,)),
        ],
        AccountType.LIABILITY: [
            (
                "current_liabilities", "Current Liabilities",
                (
                    ACCOUNTS_PAYABLE, CREDIT_CARD,
                    OTHER_CURRENT_LIABILITY, SHORT_TERM_DEBT,
                ),
            ),
            (
                "long_term_liabilities", "Long-Term Liabilities",
                (LONG_TERM_LIABILITY,),
            ),
        ],
        AccountType.EQUITY: [
            ("contributed_capital", "Contributed Capital", (OWNER_CONTRIBUTION,)),
            ("owner_distributions", "Owner Distributions", (OWNER_DISTRIBUTION,)),
            ("retained_earnings", "Retained Earnings", (RETAINED_EARNINGS,)),
            ("other_equity", "Other Equity", (OTHER_EQUITY,)),
        ],
        AccountType.REVENUE: [
            ("operating_revenue", "Operating Revenue", (OPERATING_REVENUE,)),
            (
                "other_income", "Other Income",
                (OTHER_INCOME, GAIN_ON_ASSET_DISPOSAL),
            ),
        ],
        AccountType.EXPENSE: [
            ("cost_of_goods_sold", "Cost of Goods Sold", (COST_OF_GOODS_SOLD,)),
            ("operating_expenses", "Operating Expenses", (OPERATING_EXPENSE,)),
            (
                "depreciation_amortization", "Depreciation & Amortization",
                (DEPRECIATION_AMORTIZATION,),
            ),
            (
                "other_expenses", "Other Expenses",
                (OTHER_EXPENSE, LOSS_ON_ASSET_DISPOSAL),
            ),
        ],
    }

    # Safe aliases cover LedgerTB's old seed vocabulary and common imports.
    # Ambiguous aliases that need the account name are handled in ``resolve``.
    _ALIASES = {
        AccountType.ASSET: {
            "bank": CASH,
            "bank account": CASH,
            "checking": CASH,
            "checking account": CASH,
            "savings": CASH,
            "savings account": CASH,
            "money market": CASH,
            "cash and cash equivalents": CASH,
            "receivable": ACCOUNTS_RECEIVABLE,
            "accounts receivable (a/r)": ACCOUNTS_RECEIVABLE,
            "a/r": ACCOUNTS_RECEIVABLE,
            "prepaid": OTHER_CURRENT_ASSET,
            "contra asset": ACCUMULATED_DEPRECIATION,
            "wip": INVENTORY,
            "trust": OTHER_CURRENT_ASSET,
            "equipment": FIXED_ASSET,
            "fixed assets": FIXED_ASSET,
            "vehicle": FIXED_ASSET,
            "vehicles": FIXED_ASSET,
            "property and equipment": FIXED_ASSET,
            "property, plant and equipment": FIXED_ASSET,
        },
        AccountType.LIABILITY: {
            "accrual": OTHER_CURRENT_LIABILITY,
            "deferred": OTHER_CURRENT_LIABILITY,
            "deposit": OTHER_CURRENT_LIABILITY,
            "tax": OTHER_CURRENT_LIABILITY,
            "loan": LONG_TERM_LIABILITY,
            "long term liability": LONG_TERM_LIABILITY,
            "short term debt": SHORT_TERM_DEBT,
            "line of credit": SHORT_TERM_DEBT,
            "trust": OTHER_CURRENT_LIABILITY,
        },
        AccountType.EQUITY: {
            "contributions": OWNER_CONTRIBUTION,
            "distributions": OWNER_DISTRIBUTION,
            "dividends": OWNER_DISTRIBUTION,
            "draws": OWNER_DISTRIBUTION,
            "net assets": OTHER_EQUITY,
            "guaranteed payments": OTHER_EQUITY,
        },
        AccountType.REVENUE: {
            "other": OTHER_INCOME,
            "investment": OTHER_INCOME,
            "product revenue": OPERATING_REVENUE,
            "service revenue": OPERATING_REVENUE,
            "rental": OPERATING_REVENUE,
            "contra revenue": OPERATING_REVENUE,
            "employment": OPERATING_REVENUE,
            "grants": OPERATING_REVENUE,
            "fundraising": OPERATING_REVENUE,
            "contributions": OPERATING_REVENUE,
            "program": OPERATING_REVENUE,
        },
        AccountType.EXPENSE: {
            "cogs": COST_OF_GOODS_SOLD,
            "job cost": COST_OF_GOODS_SOLD,
            "non-cash": DEPRECIATION_AMORTIZATION,
            "other": OTHER_EXPENSE,
            "interest": OTHER_EXPENSE,
            "administrative": OPERATING_EXPENSE,
            "discretionary": OPERATING_EXPENSE,
            "education": OPERATING_EXPENSE,
            "food": OPERATING_EXPENSE,
            "fundraising": OPERATING_EXPENSE,
            "giving": OPERATING_EXPENSE,
            "healthcare": OPERATING_EXPENSE,
            "housing": OPERATING_EXPENSE,
            "insurance": OPERATING_EXPENSE,
            "occupancy": OPERATING_EXPENSE,
            "operating": OPERATING_EXPENSE,
            "payroll": OPERATING_EXPENSE,
            "program": OPERATING_EXPENSE,
            "tax": OPERATING_EXPENSE,
            "transportation": OPERATING_EXPENSE,
        },
    }

    @staticmethod
    def _norm(value: str) -> str:
        return " ".join((value or "").strip().casefold().split())

    @classmethod
    def for_type(cls, account_type: str) -> list[str]:
        return list(cls.BY_TYPE.get(account_type, ()))

    @classmethod
    def statement_groups_for_type(
        cls, account_type: str
    ) -> list[tuple[str, str, tuple[str, ...]]]:
        return list(cls.STATEMENT_GROUPS.get(account_type, ()))

    @classmethod
    def is_canonical(cls, account_type: str, value: str | None) -> bool:
        return value in cls.BY_TYPE.get(account_type, ())

    @classmethod
    def resolve(
        cls, account_type: str, value: str | None, account_name: str = ""
    ) -> str | None:
        """Resolve canonical text or a safe legacy alias without mutating it."""
        if not value:
            return None
        normalized = cls._norm(value)
        for canonical in cls.BY_TYPE.get(account_type, ()):
            if normalized == cls._norm(canonical):
                return canonical

        name = cls._norm(account_name)
        if account_type == AccountType.LIABILITY and normalized == "payable":
            if "credit card" in name or any(
                token in name for token in ("visa", "mastercard", "amex")
            ):
                return cls.CREDIT_CARD
            if "accounts payable" in name or name in {"a/p", "ap"}:
                return cls.ACCOUNTS_PAYABLE
            return cls.OTHER_CURRENT_LIABILITY
        if account_type == AccountType.EQUITY and normalized == "capital":
            if "treasury" in name:
                return cls.OTHER_EQUITY
            # Corporate stock and paid-in capital are unambiguously
            # contributed capital.  A generic owner/member/partner capital
            # account can contain much more than contributions, so leave that
            # legacy value unresolved for the chart-review workflow instead of
            # silently choosing a statement group.
            if re.search(
                r"\b(?:common|preferred)\s+stock\b|\bcapital\s+stock\b|"
                r"\b(?:additional\s+)?paid[- ]in\s+capital\b|"
                r"\bcontributed\s+capital\b",
                name,
            ):
                return cls.OWNER_CONTRIBUTION
            return None
        return cls._ALIASES.get(account_type, {}).get(normalized)

    @classmethod
    def normalize_for_storage(
        cls, account_type: str, value: str | None, account_name: str = ""
    ) -> str | None:
        """Canonicalize known input; preserve unknown text for human review."""
        stripped = (value or "").strip()
        if not stripped:
            return None
        return cls.resolve(account_type, stripped, account_name) or stripped

    @classmethod
    def is_cash_like(
        cls, account_type: str, value: str | None, account_name: str = ""
    ) -> bool:
        """Identify cash accounts consistently without silently curing legacy data.

        Known aliases resolve normally. Strong name signals let reporting include
        a legacy/blank-subtype bank account in cash while still marking the
        account as needing subtype review.
        """
        if account_type != AccountType.ASSET:
            return False
        resolved = cls.resolve(account_type, value, account_name)
        if resolved is not None:
            return resolved == cls.CASH
        name = cls._norm(account_name)
        return bool(re.search(
            r"\b(?:cash|checking|savings|bank)\b|\bmoney\s+market\b",
            name,
        ))


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
