"""Book review: are these books actually right?

Three layers, in order of trust:

1. Integrity sweep — pure SQL. Questions arithmetic can answer are never
   sent to a model: balance, completeness, linkage, dating.
2. Category consistency — AI judgment over how transactions were coded,
   constrained by the client's written policy notes.
3. Analytics — ratios and trends computed deterministically; AI writes the
   reviewer's memo over the computed figures, never its own math.

Findings are suggestions with reasons. Nothing here posts, changes, or
deletes anything.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from database.connection import get_cursor
from models.account import Account
from models.reports import ReportGenerator
from models.transaction import ImportedTransaction
from money import to_dollars
from services.import_verification import check_row_continuity
from utils.untrusted import flatten_untrusted, untrusted_block


@dataclass
class ReviewFinding:
    severity: str            # "high" | "medium" | "info"
    skill: str               # "integrity" | "category" | "analytics"
    title: str
    detail: str = ""
    entry_id: Optional[int] = None
    suggested_account_number: Optional[str] = None
    suggested_account_name: Optional[str] = None


SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


# ------------------------------------------------------------- policy notes

def get_review_policy(client_id: int) -> str:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT policy FROM review_policies WHERE client_id = ?", (client_id,)
        )
        row = cursor.fetchone()
    return row["policy"] if row else ""


def set_review_policy(client_id: int, policy: str) -> None:
    with get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO review_policies (client_id, policy, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE
                SET policy = excluded.policy, updated_at = excluded.updated_at
            """,
            (client_id, policy.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


# ---------------------------------------------------------- integrity sweep

def run_integrity_sweep(client_id: int, period_start: date, period_end: date) -> List[ReviewFinding]:
    """Deterministic checks. Every finding is a fact, not an opinion."""
    findings: List[ReviewFinding] = []
    ps, pe = period_start.isoformat(), period_end.isoformat()

    with get_cursor() as cursor:
        # Entries that do not balance (should be impossible via the app; a hit
        # means direct database damage and outranks everything else).
        cursor.execute(
            """
            SELECT je.id, SUM(jel.debit) d, SUM(jel.credit) c
            FROM journal_entries je
            JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            WHERE je.client_id = ? AND je.entry_date <= ?
            GROUP BY je.id HAVING SUM(jel.debit) != SUM(jel.credit)
            """,
            (client_id, pe),
        )
        for row in cursor.fetchall():
            findings.append(ReviewFinding(
                "high", "integrity", f"Entry #{row['id']} does not balance",
                f"Debits {to_dollars(row['d']):,.2f} vs credits {to_dollars(row['c']):,.2f}.",
                entry_id=row["id"],
            ))

        cursor.execute(
            """
            SELECT je.id, COUNT(jel.id) line_count
            FROM journal_entries je
            LEFT JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            WHERE je.client_id = ? AND je.entry_date <= ?
            GROUP BY je.id HAVING COUNT(jel.id) < 2
            """,
            (client_id, pe),
        )
        for row in cursor.fetchall():
            findings.append(ReviewFinding(
                "high", "integrity", f"Entry #{row['id']} has fewer than two lines",
                "Double-entry requires at least a debit and a credit.",
                entry_id=row["id"],
            ))

        cursor.execute(
            """
            SELECT COUNT(*) n FROM imported_transactions
            WHERE client_id = ? AND status != 'Posted'
            """,
            (client_id,),
        )
        unposted = cursor.fetchone()["n"]
        if unposted:
            findings.append(ReviewFinding(
                "medium", "integrity",
                f"{unposted} imported transaction{'s' if unposted != 1 else ''} not posted",
                "Staged rows are not in the books; the trial balance excludes them.",
            ))

        cursor.execute(
            """
            SELECT COUNT(*) n FROM imported_transactions it
            WHERE it.client_id = ? AND it.status = 'Posted'
              AND (it.journal_entry_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM journal_entries je WHERE je.id = it.journal_entry_id))
            """,
            (client_id,),
        )
        broken = cursor.fetchone()["n"]
        if broken:
            findings.append(ReviewFinding(
                "high", "integrity",
                f"{broken} posted import{'s' if broken != 1 else ''} missing a journal entry link",
                "A posted import should always point at the entry it created.",
            ))

        cursor.execute(
            """
            SELECT id, entry_date FROM journal_entries
            WHERE client_id = ? AND entry_date > ?
            ORDER BY entry_date
            """,
            (client_id, date.today().isoformat()),
        )
        for row in cursor.fetchall():
            findings.append(ReviewFinding(
                "medium", "integrity", f"Entry #{row['id']} is dated in the future",
                f"Dated {row['entry_date']}.", entry_id=row["id"],
            ))

        # P&L accounts that went quiet: activity before the period, none inside it.
        cursor.execute(
            """
            SELECT a.account_number, a.name FROM accounts a
            WHERE a.client_id = ? AND a.is_active = 1 AND a.type IN ('Revenue', 'Expense')
              AND EXISTS (
                    SELECT 1 FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    WHERE jel.account_id = a.id AND je.entry_date < ?
                      AND je.entry_type NOT IN ('Beginning Balance', 'Closing'))
              AND NOT EXISTS (
                    SELECT 1 FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    WHERE jel.account_id = a.id AND je.entry_date BETWEEN ? AND ?)
            ORDER BY a.account_number
            """,
            (client_id, ps, ps, pe),
        )
        for row in cursor.fetchall():
            findings.append(ReviewFinding(
                "info", "integrity",
                f"{row['account_number']} - {row['name']} went quiet",
                "Had activity before this period but none inside it — expected, "
                "or is something unrecorded?",
            ))

    # Row continuity per import batch: a row missing from the middle of a file
    # leaves the trial balance balanced, so only this check can see it.
    for batch in ImportedTransaction.get_batch_summaries(client_id):
        rows = ImportedTransaction.get_by_batch(client_id, batch["import_batch"])
        report = check_row_continuity(rows)
        if not report.is_clean:
            missing = ", ".join(str(n) for n in report.missing_rows[:10])
            findings.append(ReviewFinding(
                "medium", "integrity",
                f"Import {batch['source_filename'] or batch['import_batch']} has row gaps",
                f"Source line number(s) missing from the middle of the file: {missing}.",
            ))

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
    return findings


# ------------------------------------------------------ category consistency

_REVIEW_TOOL = {
    "name": "report_category_findings",
    "description": "Report transactions whose account assignment looks wrong.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer",
                                  "description": "1-based transaction number from the prompt"},
                        "suggested_account_number": {"type": "string"},
                        "confidence": {"type": "string",
                                       "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string",
                                   "description": "One sentence: why the current account looks wrong"},
                    },
                    "required": ["index", "suggested_account_number", "confidence", "reason"],
                },
            }
        },
        "required": ["findings"],
    },
}

_CONFIDENCE_SEVERITY = {"high": "high", "medium": "medium", "low": "info"}

MAX_REVIEW_ROWS = 300


def _posted_transactions(client_id: int, period_start: date, period_end: date) -> List[dict]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT it.id, it.transaction_date, it.description, it.amount,
                   it.journal_entry_id,
                   ca.account_number AS category_number, ca.name AS category_name,
                   ba.account_number AS bank_number, ba.name AS bank_name
            FROM imported_transactions it
            JOIN accounts ca ON ca.id = it.suggested_account_id
            JOIN accounts ba ON ba.id = it.bank_account_id
            WHERE it.client_id = ? AND it.status = 'Posted'
              AND it.transaction_date BETWEEN ? AND ?
            ORDER BY it.transaction_date, it.id
            """,
            (client_id, period_start.isoformat(), period_end.isoformat()),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


class BookReviewService:
    """AI layers of the review. Deterministic layers never touch this class."""

    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
        self.last_error: Optional[str] = None

    def is_available(self) -> bool:
        return self.client is not None

    def review_categories(
        self, client_id: int, period_start: date, period_end: date,
        policy_notes: str = "",
    ) -> Tuple[List[ReviewFinding], int]:
        """Return (findings, transactions_reviewed)."""
        self.last_error = None
        transactions = _posted_transactions(client_id, period_start, period_end)
        if not transactions:
            return [], 0
        truncated = len(transactions) > MAX_REVIEW_ROWS
        transactions = transactions[:MAX_REVIEW_ROWS]

        accounts = Account.get_all(client_id, active_only=True)
        account_lines = "\n".join(
            f"{a.account_number} - {a.name} ({a.type})" for a in accounts
        )
        # Descriptions come from the client's bank file. A review that only
        # reports what it flags is especially worth attacking: text claiming a
        # row was "already approved by the controller" would buy silence on the
        # one transaction that deserved a look.
        txn_lines = "\n".join(
            f"{i}. {t['transaction_date']} | {flatten_untrusted(t['description'])} | "
            f"{to_dollars(t['amount']):,.2f} | from {t['bank_number']} {t['bank_name']} "
            f"| coded to {t['category_number']} - {t['category_name']}"
            for i, t in enumerate(transactions, start=1)
        )
        policy_block = (
            f"\nCLIENT ACCOUNTING POLICY (authoritative — a coding that violates "
            f"it is wrong even if plausible):\n{policy_notes}\n"
            if policy_notes.strip() else ""
        )
        prompt = (
            "You are reviewing how bank/credit-card transactions were coded in a "
            "small firm's books. Flag ONLY transactions whose account assignment "
            "looks wrong — vendor coded inconsistently across rows, a category "
            "that mismatches the merchant, or a policy violation. Do not flag "
            "codings that are defensible.\n"
            f"{policy_block}\n"
            f"CHART OF ACCOUNTS:\n{account_lines}\n\n"
            "TRANSACTIONS (amounts negative = money out of the bank account):\n"
            + untrusted_block(txn_lines, "transactions")
        )

        try:
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4000,
                tools=[_REVIEW_TOOL],
                tool_choice={"type": "tool", "name": "report_category_findings"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            self.last_error = str(exc)
            return [], len(transactions)

        raw = []
        for block in response.content:
            if block.type == "tool_use":
                raw = block.input.get("findings", [])
                break

        account_names = {a.account_number: a.name for a in accounts}
        findings = []
        for item in raw:
            index = item.get("index")
            if not isinstance(index, int) or not (1 <= index <= len(transactions)):
                continue
            t = transactions[index - 1]
            suggested = str(item.get("suggested_account_number", "")).strip()
            if suggested == t["category_number"]:
                continue  # no-op "finding"
            findings.append(ReviewFinding(
                severity=_CONFIDENCE_SEVERITY.get(item.get("confidence"), "info"),
                skill="category",
                title=(f"{t['transaction_date']} {t['description']} "
                       f"({to_dollars(t['amount']):,.2f}) — "
                       f"coded {t['category_number']} {t['category_name']}"),
                detail=item.get("reason", ""),
                entry_id=t["journal_entry_id"],
                suggested_account_number=suggested,
                suggested_account_name=account_names.get(suggested),
            ))
        if truncated:
            findings.append(ReviewFinding(
                "info", "category",
                f"Only the first {MAX_REVIEW_ROWS} transactions were reviewed",
                "Narrow the period to cover the rest.",
            ))
        findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
        return findings, len(transactions)

    def write_analytics_memo(
        self, client_name: str, period_label: str, analytics: Dict,
        policy_notes: str = "",
    ) -> Optional[str]:
        """Analytical-review prose over deterministically computed figures."""
        self.last_error = None

        def money_lines(rows):
            # Labels are "<number> - <name>", and account names arrive from
            # client-supplied QuickBooks chart exports.
            return "\n".join(
                f"  {flatten_untrusted(r['label'], 120)}: {r['value']:,.2f}"
                for r in rows
            )

        summary = (
            # The client's name adds nothing to an analytical review and would
            # be one more identifying detail leaving the machine.
            f"Figures for the period {period_label} "
            "(all computed from the ledger, not estimated):\n"
            f"Revenue {analytics['revenue']:,.2f} · Expenses {analytics['expenses']:,.2f} "
            f"· Net income {analytics['net_income']:,.2f} "
            f"· Net margin {analytics['net_margin_pct']:.1f}%\n"
            f"Cash {analytics['cash']:,.2f} · Total liabilities {analytics['liabilities']:,.2f} "
            f"· Months of expenses in cash {analytics['months_of_expenses_in_cash']:.1f}\n"
            f"Top expenses:\n{money_lines(analytics['top_expenses'])}\n"
            "Monthly revenue / expenses:\n" + "\n".join(
                f"  {m['month']}: revenue {m['revenue']:,.2f}, expenses {m['expenses']:,.2f}"
                for m in analytics['monthly']
            )
        )
        policy_block = (
            f"\nClient accounting policy notes:\n{policy_notes}\n"
            if policy_notes.strip() else ""
        )
        prompt = (
            "Write a concise analytical review memo (the kind a CPA reviewer "
            "would write) over these figures. Note what moved, what looks "
            "anomalous, and the questions a reviewer should ask management. "
            "Use ONLY the figures provided — do not invent numbers. Plain "
            "prose with a few short sections; no preamble.\n"
            f"{policy_block}\n{summary}"
        )
        try:
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            self.last_error = str(exc)
            return None
        return "".join(b.text for b in response.content if b.type == "text").strip()


book_review_service = BookReviewService()


# ------------------------------------------------------------------ analytics

def compute_analytics(client_id: int, period_start: date, period_end: date) -> Dict:
    """Ratios and trends, computed — never asked of a model."""
    income = ReportGenerator.income_statement(client_id, period_start, period_end)
    revenue = float(income["total_revenue"])
    expenses = float(income["total_expenses"])
    net_income = float(income["net_income"])

    tb = ReportGenerator.trial_balance(client_id, period_end)
    cash = sum(r.debit - r.credit for r in tb if r.account_type == "Asset"
               and any(k in r.account_name.lower() for k in ("cash", "checking", "savings")))
    liabilities = sum(r.credit - r.debit for r in tb if r.account_type == "Liability")

    top_expenses = sorted(
        ({"label": f"{e['account_number']} - {e['name']}", "value": float(e["balance"])}
         for e in income["expenses"]),
        key=lambda r: -r["value"],
    )[:6]

    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT strftime('%Y-%m', je.entry_date) month,
                   COALESCE(SUM(CASE WHEN a.type = 'Revenue'
                       THEN jel.credit - jel.debit ELSE 0 END), 0) revenue,
                   COALESCE(SUM(CASE WHEN a.type = 'Expense'
                       THEN jel.debit - jel.credit ELSE 0 END), 0) expenses
            FROM journal_entries je
            JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            JOIN accounts a ON a.id = jel.account_id
            WHERE je.client_id = ? AND je.entry_date BETWEEN ? AND ?
              AND a.type IN ('Revenue', 'Expense')
            GROUP BY month ORDER BY month
            """,
            (client_id, period_start.isoformat(), period_end.isoformat()),
        )
        monthly = [
            {"month": row["month"],
             "revenue": to_dollars(row["revenue"]),
             "expenses": to_dollars(row["expenses"])}
            for row in cursor.fetchall()
        ]

    months = max(len(monthly), 1)
    avg_monthly_expenses = expenses / months if expenses else 0.0
    return {
        "revenue": revenue,
        "expenses": expenses,
        "net_income": net_income,
        "net_margin_pct": (net_income / revenue * 100) if revenue else 0.0,
        "cash": round(cash, 2),
        "liabilities": round(liabilities, 2),
        "months_of_expenses_in_cash":
            (cash / avg_monthly_expenses) if avg_monthly_expenses else 0.0,
        "top_expenses": top_expenses,
        "monthly": monthly,
    }
