import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import date
from database.connection import get_connection
import pandas as pd


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


class ReportGenerator:

    @staticmethod
    def trial_balance(client_id: int, as_of_date: Optional[date] = None) -> List[TrialBalanceRow]:
        """Generate a trial balance report for a client."""
        conn = get_connection()
        cursor = conn.cursor()

        date_filter = ""
        date_params = []
        if as_of_date:
            date_filter = "AND je.entry_date <= ?"
            date_params.append(as_of_date.isoformat())

        # Build params in order they appear in SQL: date filter first, then client_id
        params = date_params + [client_id]

        cursor.execute(f"""
            SELECT
                a.account_number,
                a.name as account_name,
                a.type as account_type,
                COALESCE(SUM(jel.debit), 0) as total_debits,
                COALESCE(SUM(jel.credit), 0) as total_credits
            FROM accounts a
            LEFT JOIN journal_entry_lines jel ON a.id = jel.account_id
            LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id {date_filter}
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

        conn.close()
        return rows

    @staticmethod
    def income_statement(
        client_id: int,
        start_date: date,
        end_date: date
    ) -> Dict:
        """Generate an income statement for a client."""
        conn = get_connection()
        cursor = conn.cursor()

        # Get revenue accounts
        cursor.execute("""
            SELECT
                a.account_number,
                a.name,
                COALESCE(SUM(jel.credit), 0) - COALESCE(SUM(jel.debit), 0) as balance
            FROM accounts a
            LEFT JOIN journal_entry_lines jel ON a.id = jel.account_id
            LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id
                AND je.entry_date >= ? AND je.entry_date <= ?
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
            LEFT JOIN journal_entry_lines jel ON a.id = jel.account_id
            LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id
                AND je.entry_date >= ? AND je.entry_date <= ?
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

        conn.close()

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
        conn = get_connection()
        cursor = conn.cursor()

        def get_accounts_by_type(account_type: str, normal_balance: str):
            cursor.execute("""
                SELECT
                    a.account_number,
                    a.name,
                    a.subtype,
                    COALESCE(SUM(jel.debit), 0) as total_debits,
                    COALESCE(SUM(jel.credit), 0) as total_credits
                FROM accounts a
                LEFT JOIN journal_entry_lines jel ON a.id = jel.account_id
                LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id
                    AND je.entry_date <= ?
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

        total_assets = sum(a['balance'] for a in assets)
        total_liabilities = sum(l['balance'] for l in liabilities)
        total_equity = sum(e['balance'] for e in equity)

        conn.close()

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
        end_date: Optional[date] = None
    ) -> List[GeneralLedgerEntry]:
        """Generate general ledger for a specific account."""
        conn = get_connection()
        cursor = conn.cursor()

        # Get account type for balance calculation
        cursor.execute("SELECT type FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return []

        account_type = row['type']
        is_debit_normal = account_type in ('Asset', 'Expense')

        # Build query
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

        entries = []
        running_balance = 0.0

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

        conn.close()
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
            rows.append({'Item': f"  {e['account_number']} - {e['name']}", 'Amount': e['balance']})
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
                'Entry #': e.entry_id,
                'Description': e.description,
                'Reference': e.source_reference,
                'Memo': e.memo,
                'Debit': e.debit if e.debit > 0 else '',
                'Credit': e.credit if e.credit > 0 else '',
                'Balance': e.balance
            }
            for e in entries
        ])
