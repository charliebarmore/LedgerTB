import sqlite3
import calendar
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import date
from database.connection import get_cursor
import pandas as pd


def _fiscal_year_start(as_of_date: date, fiscal_year_end_month: int) -> date:
    """Return the first day of the fiscal year that as_of_date falls in."""
    last_day = calendar.monthrange(as_of_date.year, fiscal_year_end_month)[1]
    fye_this_year = date(as_of_date.year, fiscal_year_end_month, last_day)
    fy_end_year = as_of_date.year if as_of_date <= fye_this_year else as_of_date.year + 1

    start_month = fiscal_year_end_month + 1
    if start_month > 12:
        return date(fy_end_year, 1, 1)
    return date(fy_end_year - 1, start_month, 1)


@dataclass
class TrialBalanceRow:
    account_number: str
    account_name: str
    account_type: str
    debit: float
    credit: float


@dataclass
class GeneralLedgerEntry:
    entry_date: date
    entry_id: int
    description: str
    source_reference: str
    debit: float
    credit: float
    balance: float
    memo: str


@dataclass
class TrialBalanceWorksheetRow:
    """Represents a row in the CPA Trial Balance Worksheet."""
    account_id: int
    account_number: str
    account_name: str
    account_type: str
    beginning_dr: float
    beginning_cr: float
    period_debits: float      # Regular entries only
    period_credits: float     # Regular entries only
    unadjusted_dr: float
    unadjusted_cr: float
    aje_debits: float         # Adjusting entries only
    aje_credits: float
    adjusted_dr: float
    adjusted_cr: float


@dataclass
class AJEDetail:
    """Detail of a single adjusting journal entry for a specific account."""
    entry_id: int
    aje_reference: str
    entry_date: date
    description: str
    debit: float
    credit: float


class ReportGenerator:

    @staticmethod
    def trial_balance(client_id: int, as_of_date: Optional[date] = None) -> List[TrialBalanceRow]:
        """Generate a trial balance report for a client."""
        with get_cursor() as cursor:
            date_filter = ""
            date_params = []
            if as_of_date:
                date_filter = "AND je.entry_date <= ?"
                date_params.append(as_of_date.isoformat())

            # Build params in order they appear in SQL: date filter first, then client_id
            params = date_params + [client_id]

            # The date filter must live on the join between journal_entry_lines and
            # journal_entries (not just on journal_entries alone), otherwise the LEFT
            # JOIN still pulls in jel.debit/credit for out-of-range entries and the
            # date filter has no effect on the SUM().
            cursor.execute(f"""
                SELECT
                    a.account_number,
                    a.name as account_name,
                    a.type as account_type,
                    COALESCE(SUM(jel.debit), 0) as total_debits,
                    COALESCE(SUM(jel.credit), 0) as total_credits
                FROM accounts a
                LEFT JOIN (
                    journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id {date_filter}
                ) ON a.id = jel.account_id
                WHERE a.client_id = ? AND a.is_active = 1
                GROUP BY a.id, a.account_number, a.name, a.type
                HAVING total_debits > 0 OR total_credits > 0
                ORDER BY a.account_number
            """, params)

            rows = []
            for row in cursor.fetchall():
                account_type = row['account_type']
                total_debits = row['total_debits']
                total_credits = row['total_credits']

                # Calculate balance based on normal balance
                if account_type in ('Asset', 'Expense'):
                    balance = total_debits - total_credits
                    debit = balance if balance >= 0 else 0
                    credit = -balance if balance < 0 else 0
                else:  # Liability, Equity, Revenue
                    balance = total_credits - total_debits
                    credit = balance if balance >= 0 else 0
                    debit = -balance if balance < 0 else 0

                if debit != 0 or credit != 0:
                    rows.append(TrialBalanceRow(
                        account_number=row['account_number'],
                        account_name=row['account_name'],
                        account_type=account_type,
                        debit=debit,
                        credit=credit
                    ))

        return rows

    @staticmethod
    def trial_balance_worksheet(
        client_id: int,
        period_start: date,
        period_end: date,
        show_all_accounts: bool = False
    ) -> tuple[List[TrialBalanceWorksheetRow], List[Dict]]:
        """
        Generate a CPA Trial Balance Worksheet for a client.

        Args:
            client_id: The client ID
            period_start: Start date of the period
            period_end: End date of the period
            show_all_accounts: If True, show all accounts; if False, only show accounts with activity

        Returns:
            Tuple of (list of TrialBalanceWorksheetRow, list of AJE details by account)
        """
        ps, pe = period_start.isoformat(), period_end.isoformat()

        with get_cursor() as cursor:
            # Get all active accounts for the client
            cursor.execute("""
                SELECT id, account_number, name, type
                FROM accounts
                WHERE client_id = ? AND is_active = 1
                ORDER BY account_number
            """, (client_id,))
            accounts = cursor.fetchall()

            # Pull the three per-account aggregates in one grouped query each,
            # rather than ~4 queries per account. Each returns dr/cr sums keyed
            # by account_id; missing accounts default to (0, 0) below.
            def _sums(where_sql, params):
                cursor.execute(f"""
                    SELECT jel.account_id AS account_id,
                           COALESCE(SUM(jel.debit), 0) AS total_dr,
                           COALESCE(SUM(jel.credit), 0) AS total_cr
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                    WHERE je.client_id = ? {where_sql}
                    GROUP BY jel.account_id
                """, params)
                return {r['account_id']: (r['total_dr'], r['total_cr']) for r in cursor.fetchall()}

            # Beginning balance: all entry types dated before the period.
            beginning = _sums("AND je.entry_date < ?", (client_id, ps))
            # Period activity: non-adjusting entries in the period (complement of
            # the AJE bucket -- keeps Closing/Beginning Balance entries in-period,
            # per the C1 fix).
            period = _sums(
                "AND je.entry_date >= ? AND je.entry_date <= ? AND je.entry_type != 'Adjusting'",
                (client_id, ps, pe),
            )
            # AJE activity: adjusting entries in the period.
            aje = _sums(
                "AND je.entry_date >= ? AND je.entry_date <= ? AND je.entry_type = 'Adjusting'",
                (client_id, ps, pe),
            )

            # AJE line-level details, one query, grouped by account in Python.
            cursor.execute("""
                SELECT jel.account_id, je.id, je.aje_reference, je.entry_date, je.description,
                       jel.debit, jel.credit
                FROM journal_entry_lines jel
                JOIN journal_entries je ON jel.journal_entry_id = je.id
                WHERE je.client_id = ?
                  AND je.entry_date >= ? AND je.entry_date <= ?
                  AND je.entry_type = 'Adjusting'
                ORDER BY jel.account_id, je.entry_date, je.id
            """, (client_id, ps, pe))
            aje_detail_rows = cursor.fetchall()

        active_ids = {acct['id'] for acct in accounts}
        aje_details_by_account = {}
        for a in aje_detail_rows:
            if a['account_id'] not in active_ids:
                continue
            aje_details_by_account.setdefault(a['account_id'], []).append({
                'entry_id': a['id'],
                'aje_reference': a['aje_reference'] or '',
                'entry_date': date.fromisoformat(a['entry_date']),
                'description': a['description'] or '',
                'debit': a['debit'],
                'credit': a['credit']
            })

        rows = []
        for acct in accounts:
            account_id = acct['id']
            account_type = acct['type']
            is_debit_normal = account_type in ('Asset', 'Expense')

            beg_total_dr, beg_total_cr = beginning.get(account_id, (0, 0))
            period_debits, period_credits = period.get(account_id, (0, 0))
            aje_debits, aje_credits = aje.get(account_id, (0, 0))

            # Beginning balance by normal balance
            if is_debit_normal:
                beg_balance = beg_total_dr - beg_total_cr
                beginning_dr = beg_balance if beg_balance >= 0 else 0
                beginning_cr = -beg_balance if beg_balance < 0 else 0
            else:
                beg_balance = beg_total_cr - beg_total_dr
                beginning_cr = beg_balance if beg_balance >= 0 else 0
                beginning_dr = -beg_balance if beg_balance < 0 else 0

            # Unadjusted TB: Beginning + Period Activity
            if is_debit_normal:
                unadj_balance = (beginning_dr - beginning_cr) + (period_debits - period_credits)
                unadjusted_dr = unadj_balance if unadj_balance >= 0 else 0
                unadjusted_cr = -unadj_balance if unadj_balance < 0 else 0
            else:
                unadj_balance = (beginning_cr - beginning_dr) + (period_credits - period_debits)
                unadjusted_cr = unadj_balance if unadj_balance >= 0 else 0
                unadjusted_dr = -unadj_balance if unadj_balance < 0 else 0

            # Adjusted TB: Unadjusted + AJE Activity
            if is_debit_normal:
                adj_balance = (unadjusted_dr - unadjusted_cr) + (aje_debits - aje_credits)
                adjusted_dr = adj_balance if adj_balance >= 0 else 0
                adjusted_cr = -adj_balance if adj_balance < 0 else 0
            else:
                adj_balance = (unadjusted_cr - unadjusted_dr) + (aje_credits - aje_debits)
                adjusted_cr = adj_balance if adj_balance >= 0 else 0
                adjusted_dr = -adj_balance if adj_balance < 0 else 0

            # Check if account has any activity
            has_activity = (
                beginning_dr != 0 or beginning_cr != 0 or
                period_debits != 0 or period_credits != 0 or
                aje_debits != 0 or aje_credits != 0
            )

            if show_all_accounts or has_activity:
                rows.append(TrialBalanceWorksheetRow(
                    account_id=account_id,
                    account_number=acct['account_number'],
                    account_name=acct['name'],
                    account_type=account_type,
                    beginning_dr=beginning_dr,
                    beginning_cr=beginning_cr,
                    period_debits=period_debits,
                    period_credits=period_credits,
                    unadjusted_dr=unadjusted_dr,
                    unadjusted_cr=unadjusted_cr,
                    aje_debits=aje_debits,
                    aje_credits=aje_credits,
                    adjusted_dr=adjusted_dr,
                    adjusted_cr=adjusted_cr
                ))

        return rows, aje_details_by_account

    @staticmethod
    def trial_balance_worksheet_to_dataframe(
        rows: List[TrialBalanceWorksheetRow]
    ) -> pd.DataFrame:
        """Convert trial balance worksheet to pandas DataFrame."""
        return pd.DataFrame([
            {
                'Acct #': r.account_number,
                'Account Name': r.account_name,
                'Type': r.account_type,
                'Beg Bal Dr': r.beginning_dr if r.beginning_dr > 0 else '',
                'Beg Bal Cr': r.beginning_cr if r.beginning_cr > 0 else '',
                'Debits': r.period_debits if r.period_debits > 0 else '',
                'Credits': r.period_credits if r.period_credits > 0 else '',
                'Unadj TB Dr': r.unadjusted_dr if r.unadjusted_dr > 0 else '',
                'Unadj TB Cr': r.unadjusted_cr if r.unadjusted_cr > 0 else '',
                'AJE Dr': r.aje_debits if r.aje_debits > 0 else '',
                'AJE Cr': r.aje_credits if r.aje_credits > 0 else '',
                'Adj TB Dr': r.adjusted_dr if r.adjusted_dr > 0 else '',
                'Adj TB Cr': r.adjusted_cr if r.adjusted_cr > 0 else ''
            }
            for r in rows
        ])

    @staticmethod
    def income_statement(
        client_id: int,
        start_date: date,
        end_date: date
    ) -> Dict:
        """Generate an income statement for a client."""
        with get_cursor() as cursor:
            # Get revenue accounts. The date range must be applied on the join between
            # journal_entry_lines and journal_entries, not on journal_entries alone,
            # or the LEFT JOIN still includes jel.debit/credit for out-of-range entries.
            cursor.execute("""
                SELECT
                    a.account_number,
                    a.name,
                    COALESCE(SUM(jel.credit), 0) - COALESCE(SUM(jel.debit), 0) as balance
                FROM accounts a
                LEFT JOIN (
                    journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                        AND je.entry_date >= ? AND je.entry_date <= ?
                ) ON a.id = jel.account_id
                WHERE a.client_id = ? AND a.type = 'Revenue' AND a.is_active = 1
                GROUP BY a.id
                HAVING balance != 0
                ORDER BY a.account_number
            """, (start_date.isoformat(), end_date.isoformat(), client_id))

            revenues = [
                {
                    'account_number': row['account_number'],
                    'name': row['name'],
                    'balance': row['balance']
                }
                for row in cursor.fetchall()
            ]
            total_revenue = sum(r['balance'] for r in revenues)

            # Get expense accounts
            cursor.execute("""
                SELECT
                    a.account_number,
                    a.name,
                    COALESCE(SUM(jel.debit), 0) - COALESCE(SUM(jel.credit), 0) as balance
                FROM accounts a
                LEFT JOIN (
                    journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                        AND je.entry_date >= ? AND je.entry_date <= ?
                ) ON a.id = jel.account_id
                WHERE a.client_id = ? AND a.type = 'Expense' AND a.is_active = 1
                GROUP BY a.id
                HAVING balance != 0
                ORDER BY a.account_number
            """, (start_date.isoformat(), end_date.isoformat(), client_id))

            expenses = [
                {
                    'account_number': row['account_number'],
                    'name': row['name'],
                    'balance': row['balance']
                }
                for row in cursor.fetchall()
            ]
            total_expenses = sum(e['balance'] for e in expenses)

        return {
            'start_date': start_date,
            'end_date': end_date,
            'revenues': revenues,
            'total_revenue': total_revenue,
            'expenses': expenses,
            'total_expenses': total_expenses,
            'net_income': total_revenue - total_expenses
        }

    @staticmethod
    def balance_sheet(client_id: int, as_of_date: date) -> Dict:
        """Generate a balance sheet for a client."""
        with get_cursor() as cursor:
            def get_accounts_by_type(account_type: str, normal_balance: str):
                cursor.execute("""
                    SELECT
                        a.account_number,
                        a.name,
                        a.subtype,
                        COALESCE(SUM(jel.debit), 0) as total_debits,
                        COALESCE(SUM(jel.credit), 0) as total_credits
                    FROM accounts a
                    LEFT JOIN (
                        journal_entry_lines jel
                        JOIN journal_entries je ON jel.journal_entry_id = je.id
                            AND je.entry_date <= ?
                    ) ON a.id = jel.account_id
                    WHERE a.client_id = ? AND a.type = ? AND a.is_active = 1
                    GROUP BY a.id
                    ORDER BY a.account_number
                """, (as_of_date.isoformat(), client_id, account_type))

                accounts = []
                for row in cursor.fetchall():
                    if normal_balance == 'debit':
                        balance = row['total_debits'] - row['total_credits']
                    else:
                        balance = row['total_credits'] - row['total_debits']

                    if balance != 0:
                        accounts.append({
                            'account_number': row['account_number'],
                            'name': row['name'],
                            'subtype': row['subtype'],
                            'balance': balance
                        })
                return accounts

            assets = get_accounts_by_type('Asset', 'debit')
            liabilities = get_accounts_by_type('Liability', 'credit')
            equity = get_accounts_by_type('Equity', 'credit')

            # Revenue/expense activity isn't reflected anywhere in Equity until a
            # closing entry sweeps it there. Compute the un-closed net income as of
            # as_of_date and surface it as two synthetic equity lines:
            #   - Current Year Earnings: activity within the fiscal year as_of_date falls in
            #   - Retained Earnings: activity from all *prior* fiscal years
            # A posted Closing entry zeroes the revenue/expense accounts it covers, so
            # any year that has actually been closed contributes $0 here automatically
            # (its earnings already live in a real Equity account instead).
            cursor.execute("SELECT fiscal_year_end_month FROM clients WHERE id = ?", (client_id,))
            client_row = cursor.fetchone()
            fye_month = (client_row['fiscal_year_end_month'] if client_row and client_row['fiscal_year_end_month'] else 12)
            fy_start = _fiscal_year_start(as_of_date, fye_month)

            def _net_income(date_filter_sql, params):
                cursor.execute(f"""
                    SELECT
                        COALESCE(SUM(CASE WHEN a.type = 'Revenue' THEN jel.credit - jel.debit ELSE 0 END), 0) -
                        COALESCE(SUM(CASE WHEN a.type = 'Expense' THEN jel.debit - jel.credit ELSE 0 END), 0) as net_income
                    FROM accounts a
                    JOIN journal_entry_lines jel ON a.id = jel.account_id
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                    WHERE a.client_id = ? AND a.type IN ('Revenue', 'Expense') AND {date_filter_sql}
                """, [client_id] + params)
                return cursor.fetchone()['net_income'] or 0.0

            retained_earnings = _net_income("je.entry_date < ?", [fy_start.isoformat()])
            current_year_earnings = _net_income(
                "je.entry_date >= ? AND je.entry_date <= ?",
                [fy_start.isoformat(), as_of_date.isoformat()]
            )

            if retained_earnings != 0:
                equity.append({
                    'account_number': '',
                    'name': 'Retained Earnings',
                    'subtype': None,
                    'balance': retained_earnings
                })
            if current_year_earnings != 0:
                equity.append({
                    'account_number': '',
                    'name': 'Current Year Earnings',
                    'subtype': None,
                    'balance': current_year_earnings
                })

            total_assets = sum(a['balance'] for a in assets)
            total_liabilities = sum(l['balance'] for l in liabilities)
            total_equity = sum(e['balance'] for e in equity)

        return {
            'as_of_date': as_of_date,
            'assets': assets,
            'total_assets': total_assets,
            'liabilities': liabilities,
            'total_liabilities': total_liabilities,
            'equity': equity,
            'total_equity': total_equity,
            'total_liabilities_equity': total_liabilities + total_equity
        }

    @staticmethod
    def general_ledger(
        account_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        client_id: Optional[int] = None
    ) -> List[GeneralLedgerEntry]:
        """Generate general ledger for a specific account.

        If ``client_id`` is given, a ledger is produced only when the account
        belongs to that client; a cross-client id returns an empty ledger.
        """
        with get_cursor() as cursor:
            # Get account type for balance calculation (scoped to the client when given)
            if client_id is None:
                cursor.execute("SELECT type FROM accounts WHERE id = ?", (account_id,))
            else:
                cursor.execute(
                    "SELECT type FROM accounts WHERE id = ? AND client_id = ?",
                    (account_id, client_id)
                )
            row = cursor.fetchone()
            if not row:
                return []

            account_type = row['type']
            is_debit_normal = account_type in ('Asset', 'Expense')

            entries = []
            running_balance = 0.0

            # Calculate beginning balance if start_date is specified
            if start_date:
                cursor.execute("""
                    SELECT COALESCE(SUM(jel.debit), 0) as total_dr,
                           COALESCE(SUM(jel.credit), 0) as total_cr
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                    WHERE jel.account_id = ? AND je.entry_date < ?
                """, (account_id, start_date.isoformat()))

                beg_row = cursor.fetchone()
                beg_total_dr = beg_row['total_dr']
                beg_total_cr = beg_row['total_cr']

                if is_debit_normal:
                    running_balance = beg_total_dr - beg_total_cr
                else:
                    running_balance = beg_total_cr - beg_total_dr

                # Add beginning balance entry if there's a balance
                if running_balance != 0:
                    entries.append(GeneralLedgerEntry(
                        entry_date=start_date,
                        entry_id=0,
                        description="Beginning Balance",
                        source_reference="",
                        debit=0,
                        credit=0,
                        balance=running_balance,
                        memo=""
                    ))

            # Build query for period transactions
            query = """
                SELECT
                    je.entry_date,
                    je.id as entry_id,
                    je.description,
                    je.source_reference,
                    jel.debit,
                    jel.credit,
                    jel.memo
                FROM journal_entry_lines jel
                JOIN journal_entries je ON jel.journal_entry_id = je.id
                WHERE jel.account_id = ?
            """
            params = [account_id]

            if start_date:
                query += " AND je.entry_date >= ?"
                params.append(start_date.isoformat())

            if end_date:
                query += " AND je.entry_date <= ?"
                params.append(end_date.isoformat())

            query += " ORDER BY je.entry_date, je.id"

            cursor.execute(query, params)

            for row in cursor.fetchall():
                debit = row['debit']
                credit = row['credit']

                if is_debit_normal:
                    running_balance += debit - credit
                else:
                    running_balance += credit - debit

                entries.append(GeneralLedgerEntry(
                    entry_date=date.fromisoformat(row['entry_date']),
                    entry_id=row['entry_id'],
                    description=row['description'] or '',
                    source_reference=row['source_reference'] or '',
                    debit=debit,
                    credit=credit,
                    balance=running_balance,
                    memo=row['memo'] or ''
                ))

        return entries

    @staticmethod
    def trial_balance_to_dataframe(rows: List[TrialBalanceRow]) -> pd.DataFrame:
        """Convert trial balance to pandas DataFrame for export."""
        return pd.DataFrame([
            {
                'Account Number': r.account_number,
                'Account Name': r.account_name,
                'Account Type': r.account_type,
                'Debit': r.debit if r.debit > 0 else '',
                'Credit': r.credit if r.credit > 0 else ''
            }
            for r in rows
        ])

    @staticmethod
    def income_statement_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert income statement to pandas DataFrame for export."""
        rows = []

        rows.append({'Item': 'REVENUE', 'Amount': ''})
        for r in report['revenues']:
            rows.append({'Item': f"  {r['account_number']} - {r['name']}", 'Amount': r['balance']})
        rows.append({'Item': 'Total Revenue', 'Amount': report['total_revenue']})

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'EXPENSES', 'Amount': ''})
        for e in report['expenses']:
            rows.append({'Item': f"  {e['account_number']} - {e['name']}", 'Amount': e['balance']})
        rows.append({'Item': 'Total Expenses', 'Amount': report['total_expenses']})

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'NET INCOME', 'Amount': report['net_income']})

        return pd.DataFrame(rows)

    @staticmethod
    def balance_sheet_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert balance sheet to pandas DataFrame for export."""
        rows = []

        rows.append({'Item': 'ASSETS', 'Amount': ''})
        for a in report['assets']:
            rows.append({'Item': f"  {a['account_number']} - {a['name']}", 'Amount': a['balance']})
        rows.append({'Item': 'Total Assets', 'Amount': report['total_assets']})

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'LIABILITIES', 'Amount': ''})
        for l in report['liabilities']:
            rows.append({'Item': f"  {l['account_number']} - {l['name']}", 'Amount': l['balance']})
        rows.append({'Item': 'Total Liabilities', 'Amount': report['total_liabilities']})

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'EQUITY', 'Amount': ''})
        for e in report['equity']:
            label = f"  {e['account_number']} - {e['name']}" if e['account_number'] else f"  {e['name']}"
            rows.append({'Item': label, 'Amount': e['balance']})
        rows.append({'Item': 'Total Equity', 'Amount': report['total_equity']})

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'TOTAL LIABILITIES & EQUITY', 'Amount': report['total_liabilities_equity']})

        return pd.DataFrame(rows)

    @staticmethod
    def general_ledger_to_dataframe(entries: List[GeneralLedgerEntry]) -> pd.DataFrame:
        """Convert general ledger to pandas DataFrame for export."""
        return pd.DataFrame([
            {
                'Date': e.entry_date.isoformat(),
                'Entry #': '' if e.entry_id == 0 else e.entry_id,
                'Description': e.description,
                'Reference': e.source_reference,
                'Memo': e.memo,
                'Debit': e.debit if e.debit > 0 else '',
                'Credit': e.credit if e.credit > 0 else '',
                'Balance': e.balance
            }
            for e in entries
        ])
