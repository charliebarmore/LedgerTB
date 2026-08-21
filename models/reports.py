import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import date
from database.connection import get_cursor
from constants import AccountSubtype, AccountType
from money import to_dollars
from utils.fiscal_dates import (
    fiscal_year_bounds,
    prior_year_date,
    prior_year_period,
    require_valid_range,
)
import pandas as pd


def _fiscal_year_start(as_of_date: date, fiscal_year_end_month: int) -> date:
    """Return the first day of the fiscal year that as_of_date falls in."""
    return fiscal_year_bounds(as_of_date, fiscal_year_end_month)[0]


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
    def _has_history(client_id: int, through_date: date) -> bool:
        """Whether the book existed by a comparison date.

        This is intentionally broader than "had P&L activity in the period":
        an established book with a quiet prior period has a meaningful zero,
        while a book first opened this year has no prior-year result at all.
        """
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM journal_entries
                WHERE client_id = ? AND entry_date <= ?
                LIMIT 1
                """,
                (client_id, through_date.isoformat()),
            )
            return cursor.fetchone() is not None

    @staticmethod
    def _comparison_value(current: float, prior: float, available: bool) -> Dict:
        if not available:
            return {
                'current': round(current, 2),
                'prior': None,
                'change': None,
                'change_percent': None,
            }
        change = round(current - prior, 2)
        return {
            'current': round(current, 2),
            'prior': round(prior, 2),
            'change': change,
            'change_percent': (
                None if round(prior, 2) == 0
                else round((change / abs(prior)) * 100, 2)
            ),
        }

    @staticmethod
    def _merge_statement_lines(
        current: List[Dict], prior: List[Dict], available: bool
    ) -> List[Dict]:
        """Merge statement lines without dropping accounts unique to a year."""
        def key(item):
            return (item.get('account_number') or '', item['name'])

        current_by_key = {key(item): item for item in current}
        prior_by_key = {key(item): item for item in prior}
        ordered_keys = list(current_by_key)
        ordered_keys.extend(k for k in prior_by_key if k not in current_by_key)

        rows = []
        for line_key in ordered_keys:
            current_item = current_by_key.get(line_key)
            prior_item = prior_by_key.get(line_key)
            source = current_item or prior_item
            row = {
                'account_number': source.get('account_number') or '',
                'name': source['name'],
            }
            if 'subtype' in source:
                row['subtype'] = source.get('subtype')
            row.update(ReportGenerator._comparison_value(
                current_item['balance'] if current_item else 0.0,
                prior_item['balance'] if prior_item else 0.0,
                available,
            ))
            rows.append(row)
        return rows

    @staticmethod
    def _resolved_statement_subtype(item: Dict, account_type: str) -> Optional[str]:
        # Balance-sheet earnings are synthetic rows, not chart accounts. They
        # belong in retained earnings without changing the established flat
        # line contract, where their raw subtype remains None.
        if (
            account_type == AccountType.EQUITY
            and not item.get('account_number')
            and item.get('name') in {'Retained Earnings', 'Current Year Earnings'}
        ):
            return AccountSubtype.RETAINED_EARNINGS
        return AccountSubtype.resolve(
            account_type, item.get('subtype'), item.get('name', '')
        )

    @staticmethod
    def _group_statement_lines(
        items: List[Dict],
        account_type: str,
        *,
        comparative: bool = False,
        prior_available: bool = True,
    ) -> List[Dict]:
        """Group flat statement lines without altering the flat contract."""
        definitions = AccountSubtype.statement_groups_for_type(account_type)
        buckets = {
            key: {
                'key': key,
                'group': label,
                'subtypes': list(subtypes),
                'accounts': [],
            }
            for key, label, subtypes in definitions
        }
        subtype_to_key = {
            subtype: key
            for key, _label, subtypes in definitions
            for subtype in subtypes
        }
        unclassified = {
            'key': 'unclassified',
            'group': f"Unclassified {AccountType.plural_label(account_type)}",
            'subtypes': [],
            'accounts': [],
        }

        for item in items:
            statement_subtype = ReportGenerator._resolved_statement_subtype(
                item, account_type
            )
            grouped_item = dict(item)
            grouped_item['statement_subtype'] = statement_subtype
            key = subtype_to_key.get(statement_subtype)
            target = buckets.get(key) if key else unclassified
            target['accounts'].append(grouped_item)

        ordered = [buckets[key] for key, _label, _subtypes in definitions]
        ordered.append(unclassified)
        groups = [group for group in ordered if group['accounts']]
        for group in groups:
            if comparative:
                current = sum(item['current'] for item in group['accounts'])
                prior = sum(
                    item['prior'] or 0 for item in group['accounts']
                )
                group['subtotal'] = ReportGenerator._comparison_value(
                    current, prior, prior_available
                )
            else:
                group['subtotal'] = sum(
                    item['balance'] for item in group['accounts']
                )
        return groups

    @staticmethod
    def _groups_to_dollars(groups: List[Dict]) -> None:
        for group in groups:
            group['subtotal'] = to_dollars(group['subtotal'])
            for item in group['accounts']:
                item['balance'] = to_dollars(item['balance'])

    @staticmethod
    def _group_subtotal(groups: List[Dict], key: str) -> int:
        return next(
            (group['subtotal'] for group in groups if group['key'] == key), 0
        )

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
                WHERE a.client_id = ?
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
                if AccountType.is_debit_normal(account_type):
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
                        debit=to_dollars(debit),
                        credit=to_dollars(credit)
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
        require_valid_range(period_start, period_end, "Worksheet period")
        ps, pe = period_start.isoformat(), period_end.isoformat()

        with get_cursor() as cursor:
            # Historical reports must retain deactivated accounts when they have
            # activity. Deactivation prevents future posting; it must not erase an
            # account from prior-period financial statements. Never-used inactive
            # accounts remain hidden from the worksheet's "show all" view.
            cursor.execute("""
                SELECT id, account_number, name, type
                FROM accounts
                WHERE client_id = ?
                  AND (
                      is_active = 1
                      OR EXISTS (
                          SELECT 1 FROM journal_entry_lines jel
                          WHERE jel.account_id = accounts.id
                      )
                  )
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

        reportable_ids = {acct['id'] for acct in accounts}
        aje_details_by_account = {}
        for a in aje_detail_rows:
            if a['account_id'] not in reportable_ids:
                continue
            aje_details_by_account.setdefault(a['account_id'], []).append({
                'entry_id': a['id'],
                'aje_reference': a['aje_reference'] or '',
                'entry_date': date.fromisoformat(a['entry_date']),
                'description': a['description'] or '',
                'debit': to_dollars(a['debit']),
                'credit': to_dollars(a['credit'])
            })

        rows = []
        for acct in accounts:
            account_id = acct['id']
            account_type = acct['type']
            is_debit_normal = AccountType.is_debit_normal(account_type)

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
                    beginning_dr=to_dollars(beginning_dr),
                    beginning_cr=to_dollars(beginning_cr),
                    period_debits=to_dollars(period_debits),
                    period_credits=to_dollars(period_credits),
                    unadjusted_dr=to_dollars(unadjusted_dr),
                    unadjusted_cr=to_dollars(unadjusted_cr),
                    aje_debits=to_dollars(aje_debits),
                    aje_credits=to_dollars(aje_credits),
                    adjusted_dr=to_dollars(adjusted_dr),
                    adjusted_cr=to_dollars(adjusted_cr)
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
        require_valid_range(start_date, end_date, "Income statement")
        with get_cursor() as cursor:
            # Get revenue accounts. The date range must be applied on the join between
            # journal_entry_lines and journal_entries, not on journal_entries alone,
            # or the LEFT JOIN still includes jel.debit/credit for out-of-range entries.
            cursor.execute("""
                SELECT
                    a.account_number,
                    a.name,
                    a.subtype,
                    COALESCE(SUM(jel.credit), 0) - COALESCE(SUM(jel.debit), 0) as balance
                FROM accounts a
                LEFT JOIN (
                    journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                        AND je.entry_date >= ? AND je.entry_date <= ?
                ) ON a.id = jel.account_id
                WHERE a.client_id = ? AND a.type = 'Revenue'
                GROUP BY a.id
                HAVING balance != 0
                ORDER BY a.account_number
            """, (start_date.isoformat(), end_date.isoformat(), client_id))

            revenues = [
                {
                    'account_number': row['account_number'],
                    'name': row['name'],
                    'subtype': row['subtype'],
                    'balance': row['balance']  # cents
                }
                for row in cursor.fetchall()
            ]
            total_revenue = sum(r['balance'] for r in revenues)  # cents, exact

            # Get expense accounts
            cursor.execute("""
                SELECT
                    a.account_number,
                    a.name,
                    a.subtype,
                    COALESCE(SUM(jel.debit), 0) - COALESCE(SUM(jel.credit), 0) as balance
                FROM accounts a
                LEFT JOIN (
                    journal_entry_lines jel
                    JOIN journal_entries je ON jel.journal_entry_id = je.id
                        AND je.entry_date >= ? AND je.entry_date <= ?
                ) ON a.id = jel.account_id
                WHERE a.client_id = ? AND a.type = 'Expense'
                GROUP BY a.id
                HAVING balance != 0
                ORDER BY a.account_number
            """, (start_date.isoformat(), end_date.isoformat(), client_id))

            expenses = [
                {
                    'account_number': row['account_number'],
                    'name': row['name'],
                    'subtype': row['subtype'],
                    'balance': row['balance']  # cents
                }
                for row in cursor.fetchall()
            ]
            total_expenses = sum(e['balance'] for e in expenses)  # cents, exact

        revenue_groups = ReportGenerator._group_statement_lines(
            revenues, AccountType.REVENUE
        )
        expense_groups = ReportGenerator._group_statement_lines(
            expenses, AccountType.EXPENSE
        )
        operating_revenue = ReportGenerator._group_subtotal(
            revenue_groups, 'operating_revenue'
        )
        other_income = ReportGenerator._group_subtotal(
            revenue_groups, 'other_income'
        )
        cost_of_goods_sold = ReportGenerator._group_subtotal(
            expense_groups, 'cost_of_goods_sold'
        )
        operating_expenses = ReportGenerator._group_subtotal(
            expense_groups, 'operating_expenses'
        )
        depreciation_amortization = ReportGenerator._group_subtotal(
            expense_groups, 'depreciation_amortization'
        )
        other_expenses = ReportGenerator._group_subtotal(
            expense_groups, 'other_expenses'
        )
        gross_profit = operating_revenue - cost_of_goods_sold
        operating_income = (
            gross_profit - operating_expenses - depreciation_amortization
        )

        # All aggregation above is in exact integer cents; convert to dollars for output.
        ReportGenerator._groups_to_dollars(revenue_groups)
        ReportGenerator._groups_to_dollars(expense_groups)
        for r in revenues:
            r['balance'] = to_dollars(r['balance'])
        for e in expenses:
            e['balance'] = to_dollars(e['balance'])

        return {
            'start_date': start_date,
            'end_date': end_date,
            'revenues': revenues,
            'revenue_groups': revenue_groups,
            'total_revenue': to_dollars(total_revenue),
            'expenses': expenses,
            'expense_groups': expense_groups,
            'total_expenses': to_dollars(total_expenses),
            'operating_revenue': to_dollars(operating_revenue),
            'other_income': to_dollars(other_income),
            'cost_of_goods_sold': to_dollars(cost_of_goods_sold),
            'gross_profit': to_dollars(gross_profit),
            'operating_expenses': to_dollars(operating_expenses),
            'depreciation_amortization': to_dollars(depreciation_amortization),
            'operating_income': to_dollars(operating_income),
            'other_expenses': to_dollars(other_expenses),
            'net_income': to_dollars(total_revenue - total_expenses)
        }

    @staticmethod
    def comparative_income_statement(
        client_id: int,
        start_date: date,
        end_date: date,
    ) -> Dict:
        """Income statement with the same prior-year period alongside it."""
        require_valid_range(start_date, end_date, "Income statement")
        prior_start, prior_end = prior_year_period(start_date, end_date)
        current = ReportGenerator.income_statement(client_id, start_date, end_date)
        prior = ReportGenerator.income_statement(client_id, prior_start, prior_end)
        available = ReportGenerator._has_history(client_id, prior_end)
        revenues = ReportGenerator._merge_statement_lines(
            current['revenues'], prior['revenues'], available
        )
        expenses = ReportGenerator._merge_statement_lines(
            current['expenses'], prior['expenses'], available
        )
        return {
            'current_period': {'start': start_date, 'end': end_date},
            'prior_period': {'start': prior_start, 'end': prior_end},
            'prior_available': available,
            'revenues': revenues,
            'revenue_groups': ReportGenerator._group_statement_lines(
                revenues, AccountType.REVENUE, comparative=True,
                prior_available=available,
            ),
            'expenses': expenses,
            'expense_groups': ReportGenerator._group_statement_lines(
                expenses, AccountType.EXPENSE, comparative=True,
                prior_available=available,
            ),
            'total_revenue': ReportGenerator._comparison_value(
                current['total_revenue'], prior['total_revenue'], available
            ),
            'total_expenses': ReportGenerator._comparison_value(
                current['total_expenses'], prior['total_expenses'], available
            ),
            'operating_revenue': ReportGenerator._comparison_value(
                current['operating_revenue'], prior['operating_revenue'], available
            ),
            'other_income': ReportGenerator._comparison_value(
                current['other_income'], prior['other_income'], available
            ),
            'cost_of_goods_sold': ReportGenerator._comparison_value(
                current['cost_of_goods_sold'], prior['cost_of_goods_sold'], available
            ),
            'gross_profit': ReportGenerator._comparison_value(
                current['gross_profit'], prior['gross_profit'], available
            ),
            'operating_expenses': ReportGenerator._comparison_value(
                current['operating_expenses'], prior['operating_expenses'], available
            ),
            'depreciation_amortization': ReportGenerator._comparison_value(
                current['depreciation_amortization'],
                prior['depreciation_amortization'], available,
            ),
            'operating_income': ReportGenerator._comparison_value(
                current['operating_income'], prior['operating_income'], available
            ),
            'other_expenses': ReportGenerator._comparison_value(
                current['other_expenses'], prior['other_expenses'], available
            ),
            'net_income': ReportGenerator._comparison_value(
                current['net_income'], prior['net_income'], available
            ),
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
                    WHERE a.client_id = ? AND a.type = ?
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

            total_assets = sum(a['balance'] for a in assets)          # cents
            total_liabilities = sum(l['balance'] for l in liabilities)  # cents
            total_equity = sum(e['balance'] for e in equity)          # cents

        asset_groups = ReportGenerator._group_statement_lines(
            assets, AccountType.ASSET
        )
        liability_groups = ReportGenerator._group_statement_lines(
            liabilities, AccountType.LIABILITY
        )
        equity_groups = ReportGenerator._group_statement_lines(
            equity, AccountType.EQUITY
        )

        # All aggregation above is in exact integer cents; convert to dollars for output.
        for statement_groups in (asset_groups, liability_groups, equity_groups):
            ReportGenerator._groups_to_dollars(statement_groups)
        for group in (assets, liabilities, equity):
            for item in group:
                item['balance'] = to_dollars(item['balance'])

        return {
            'as_of_date': as_of_date,
            'assets': assets,
            'asset_groups': asset_groups,
            'total_assets': to_dollars(total_assets),
            'liabilities': liabilities,
            'liability_groups': liability_groups,
            'total_liabilities': to_dollars(total_liabilities),
            'equity': equity,
            'equity_groups': equity_groups,
            'total_equity': to_dollars(total_equity),
            'total_liabilities_equity': to_dollars(total_liabilities + total_equity)
        }

    @staticmethod
    def comparative_balance_sheet(client_id: int, as_of_date: date) -> Dict:
        """Balance sheet with the same date one year earlier alongside it."""
        prior_as_of = prior_year_date(as_of_date)
        current = ReportGenerator.balance_sheet(client_id, as_of_date)
        prior = ReportGenerator.balance_sheet(client_id, prior_as_of)
        available = ReportGenerator._has_history(client_id, prior_as_of)
        assets = ReportGenerator._merge_statement_lines(
            current['assets'], prior['assets'], available
        )
        liabilities = ReportGenerator._merge_statement_lines(
            current['liabilities'], prior['liabilities'], available
        )
        equity = ReportGenerator._merge_statement_lines(
            current['equity'], prior['equity'], available
        )
        return {
            'current_as_of': as_of_date,
            'prior_as_of': prior_as_of,
            'prior_available': available,
            'assets': assets,
            'asset_groups': ReportGenerator._group_statement_lines(
                assets, AccountType.ASSET, comparative=True,
                prior_available=available,
            ),
            'liabilities': liabilities,
            'liability_groups': ReportGenerator._group_statement_lines(
                liabilities, AccountType.LIABILITY, comparative=True,
                prior_available=available,
            ),
            'equity': equity,
            'equity_groups': ReportGenerator._group_statement_lines(
                equity, AccountType.EQUITY, comparative=True,
                prior_available=available,
            ),
            'total_assets': ReportGenerator._comparison_value(
                current['total_assets'], prior['total_assets'], available
            ),
            'total_liabilities': ReportGenerator._comparison_value(
                current['total_liabilities'], prior['total_liabilities'], available
            ),
            'total_equity': ReportGenerator._comparison_value(
                current['total_equity'], prior['total_equity'], available
            ),
            'total_liabilities_equity': ReportGenerator._comparison_value(
                current['total_liabilities_equity'],
                prior['total_liabilities_equity'],
                available,
            ),
            'current_balanced': (
                round(current['total_assets'], 2)
                == round(current['total_liabilities_equity'], 2)
            ),
            'prior_balanced': (
                None if not available else
                round(prior['total_assets'], 2)
                == round(prior['total_liabilities_equity'], 2)
            ),
        }

    @staticmethod
    def cash_flow_statement(
        client_id: int,
        start_date: date,
        end_date: date,
    ) -> Dict:
        """Derived statement of cash flows using the indirect method.

        Operating cash starts with period profit (excluding Beginning Balance
        and Closing entries), then reconciles noncash and working-capital
        changes. Investing and financing amounts come from cash-affecting
        journal entries rather than raw balance deltas. Ambiguous entries stay
        explicit in an unclassified section; they are never silently forced
        into Operating merely to make the statement look complete.
        """
        require_valid_range(start_date, end_date, "Cash flow statement")
        start_iso, end_iso = start_date.isoformat(), end_date.isoformat()

        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT a.id AS account_id, a.account_number, a.name,
                       a.type AS account_type, a.subtype,
                       COALESCE(SUM(CASE
                           WHEN je.entry_date < ?
                             OR (je.entry_date = ?
                                 AND je.entry_type = 'Beginning Balance')
                           THEN jel.debit - jel.credit ELSE 0 END), 0)
                           AS opening_debit_balance,
                       COALESCE(SUM(CASE WHEN je.entry_date <= ?
                           THEN jel.debit - jel.credit ELSE 0 END), 0)
                           AS ending_debit_balance
                FROM accounts a
                LEFT JOIN journal_entry_lines jel ON jel.account_id = a.id
                LEFT JOIN journal_entries je ON je.id = jel.journal_entry_id
                WHERE a.client_id = ?
                GROUP BY a.id
                ORDER BY a.account_number
                """,
                (start_iso, start_iso, end_iso, client_id),
            )
            balance_rows = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT je.id AS entry_id, je.entry_date, je.entry_type,
                       je.description, a.id AS account_id, a.account_number,
                       a.name, a.type AS account_type, a.subtype,
                       jel.debit, jel.credit
                FROM journal_entries je
                JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
                JOIN accounts a ON a.id = jel.account_id
                WHERE je.client_id = ? AND je.entry_date BETWEEN ? AND ?
                ORDER BY je.entry_date, je.id, jel.id
                """,
                (client_id, start_iso, end_iso),
            )
            activity_rows = [dict(row) for row in cursor.fetchall()]

        def resolved(row):
            return AccountSubtype.resolve(
                row['account_type'], row.get('subtype'), row.get('name', '')
            )

        cash_rows = [
            row for row in balance_rows
            if row['account_type'] == AccountType.ASSET
            and resolved(row) == AccountSubtype.CASH
        ]
        cash_account_ids = {row['account_id'] for row in cash_rows}
        cash_beginning = sum(row['opening_debit_balance'] for row in cash_rows)
        cash_ending = sum(row['ending_debit_balance'] for row in cash_rows)
        actual_cash_change = cash_ending - cash_beginning

        # Period profit and its noncash adjustments come from activity, not the
        # current income-statement output, because a posted Closing entry must
        # not erase the period's earnings from this reconciliation.
        net_income = 0
        depreciation_amortization = 0
        gain_adjustment = 0
        loss_adjustment = 0
        for row in activity_rows:
            if row['entry_type'] in ('Beginning Balance', 'Closing'):
                continue
            amount = row['credit'] - row['debit']
            subtype = resolved(row)
            if row['account_type'] == AccountType.REVENUE:
                net_income += amount
                if subtype == AccountSubtype.GAIN_ON_ASSET_DISPOSAL:
                    gain_adjustment -= amount
            elif row['account_type'] == AccountType.EXPENSE:
                expense = -amount
                net_income -= expense
                if subtype == AccountSubtype.DEPRECIATION_AMORTIZATION:
                    depreciation_amortization += expense
                elif subtype == AccountSubtype.LOSS_ON_ASSET_DISPOSAL:
                    loss_adjustment += expense

        balance_by_subtype = {}
        for row in balance_rows:
            subtype = resolved(row)
            if not subtype:
                continue
            multiplier = (
                1 if row['account_type'] in AccountType.DEBIT_NORMAL else -1
            )
            opening = row['opening_debit_balance'] * multiplier
            ending = row['ending_debit_balance'] * multiplier
            values = balance_by_subtype.setdefault(
                subtype, {'opening': 0, 'ending': 0, 'account_ids': []}
            )
            values['opening'] += opening
            values['ending'] += ending
            values['account_ids'].append(row['account_id'])

        operating_lines = [
            {'key': 'net_income', 'name': 'Net Income', 'amount': net_income},
        ]
        if depreciation_amortization:
            operating_lines.append({
                'key': 'depreciation_amortization',
                'name': 'Depreciation & Amortization',
                'amount': depreciation_amortization,
            })
        if gain_adjustment:
            operating_lines.append({
                'key': 'gains_on_asset_disposals',
                'name': 'Gains on Asset Disposals',
                'amount': gain_adjustment,
            })
        if loss_adjustment:
            operating_lines.append({
                'key': 'losses_on_asset_disposals',
                'name': 'Losses on Asset Disposals',
                'amount': loss_adjustment,
            })

        working_capital = [
            (AccountSubtype.ACCOUNTS_RECEIVABLE, 'accounts_receivable',
             'Change in Accounts Receivable', -1),
            (AccountSubtype.INVENTORY, 'inventory',
             'Change in Inventory', -1),
            (AccountSubtype.OTHER_CURRENT_ASSET, 'other_current_assets',
             'Change in Other Current Assets', -1),
            (AccountSubtype.ACCOUNTS_PAYABLE, 'accounts_payable',
             'Change in Accounts Payable', 1),
            (AccountSubtype.CREDIT_CARD, 'credit_cards',
             'Change in Credit Cards', 1),
            (AccountSubtype.OTHER_CURRENT_LIABILITY,
             'other_current_liabilities',
             'Change in Other Current Liabilities', 1),
        ]
        for subtype, key, label, direction in working_capital:
            values = balance_by_subtype.get(subtype)
            if not values:
                continue
            adjustment = direction * (values['ending'] - values['opening'])
            if adjustment:
                operating_lines.append({
                    'key': key,
                    'name': label,
                    'amount': adjustment,
                    'account_ids': values['account_ids'],
                })

        entries = {}
        for row in activity_rows:
            entries.setdefault(row['entry_id'], []).append(row)

        section_activity = {
            'operating': {}, 'investing': {}, 'financing': {},
            'unclassified': {},
        }
        unclassified_entries = []
        noncash_items = []

        def counterpart_section(row):
            subtype = resolved(row)
            account_type = row['account_type']
            if account_type in (AccountType.REVENUE, AccountType.EXPENSE):
                return 'operating'
            if account_type == AccountType.ASSET:
                if subtype in (
                    AccountSubtype.ACCOUNTS_RECEIVABLE,
                    AccountSubtype.INVENTORY,
                    AccountSubtype.OTHER_CURRENT_ASSET,
                ):
                    return 'operating'
                if subtype in (
                    AccountSubtype.FIXED_ASSET,
                    AccountSubtype.ACCUMULATED_DEPRECIATION,
                    AccountSubtype.OTHER_ASSET,
                ):
                    return 'investing'
                return 'unclassified'
            if account_type == AccountType.LIABILITY:
                if subtype in (
                    AccountSubtype.ACCOUNTS_PAYABLE,
                    AccountSubtype.CREDIT_CARD,
                    AccountSubtype.OTHER_CURRENT_LIABILITY,
                ):
                    return 'operating'
                if subtype in (
                    AccountSubtype.SHORT_TERM_DEBT,
                    AccountSubtype.LONG_TERM_LIABILITY,
                ):
                    return 'financing'
                return 'unclassified'
            if account_type == AccountType.EQUITY:
                if subtype in (
                    AccountSubtype.OWNER_CONTRIBUTION,
                    AccountSubtype.OWNER_DISTRIBUTION,
                    AccountSubtype.RETAINED_EARNINGS,
                    AccountSubtype.OTHER_EQUITY,
                ):
                    return 'financing'
                return 'unclassified'
            return 'unclassified'

        def add_section_amount(section, target, amount, entry_id):
            key = target['account_id'] if target else 0
            fallback_names = {
                'operating': 'Net Operating Cash Activity',
                'investing': 'Net Investing Cash Activity',
                'financing': 'Net Financing Cash Activity',
                'unclassified': 'Mixed or Unclassified Activity',
            }
            line = section_activity[section].setdefault(key, {
                'account_id': target['account_id'] if target else None,
                'account_number': target['account_number'] if target else '',
                'name': target['name'] if target else fallback_names[section],
                'amount': 0,
                'entry_ids': [],
            })
            line['amount'] += amount
            line['entry_ids'].append(entry_id)

        for entry_id, lines in entries.items():
            cash_lines = [
                line for line in lines if line['account_id'] in cash_account_ids
            ]
            cash_change = sum(
                line['debit'] - line['credit'] for line in cash_lines
            )
            counterparts = [
                line for line in lines if line['account_id'] not in cash_account_ids
            ]

            # A start-date Beginning Balance establishes opening cash; it is
            # deliberately not presented as a current-period cash flow.
            if (
                lines[0]['entry_type'] == 'Beginning Balance'
                and lines[0]['entry_date'] == start_iso
            ):
                continue

            if lines[0]['entry_type'] == 'Closing' and not cash_change:
                continue

            counterpart_sections = {
                counterpart_section(line) for line in counterparts
            }
            if not cash_change:
                meaningful_noncash = [
                    line for line in counterparts
                    if counterpart_section(line) in ('investing', 'financing')
                    and resolved(line) != AccountSubtype.ACCUMULATED_DEPRECIATION
                ]
                if meaningful_noncash:
                    noncash_items.append({
                        'entry_id': entry_id,
                        'entry_date': lines[0]['entry_date'],
                        'description': lines[0]['description'] or '',
                        'accounts': [line['account_number'] for line in meaningful_noncash],
                    })
                continue

            reason = None
            if lines[0]['entry_type'] in ('Beginning Balance', 'Closing'):
                section = 'unclassified'
                reason = f"{lines[0]['entry_type']} entry affects cash"
            elif 'unclassified' in counterpart_sections or not counterparts:
                section = 'unclassified'
                reason = 'counterpart account needs a statement subtype'
            elif {'investing', 'financing'} <= counterpart_sections:
                section = 'unclassified'
                reason = 'entry mixes investing and financing activity'
            elif 'financing' in counterpart_sections and 'operating' in counterpart_sections:
                section = 'unclassified'
                reason = 'entry mixes financing and operating activity'
            elif 'investing' in counterpart_sections and 'operating' in counterpart_sections:
                operating_subtypes = {
                    resolved(line) for line in counterparts
                    if counterpart_section(line) == 'operating'
                }
                disposal_adjusters = {
                    AccountSubtype.GAIN_ON_ASSET_DISPOSAL,
                    AccountSubtype.LOSS_ON_ASSET_DISPOSAL,
                }
                if operating_subtypes <= disposal_adjusters:
                    section = 'investing'
                else:
                    section = 'unclassified'
                    reason = 'entry mixes investing and operating activity'
            elif 'investing' in counterpart_sections:
                section = 'investing'
            elif 'financing' in counterpart_sections:
                section = 'financing'
            else:
                section = 'operating'

            matching_targets = [
                line for line in counterparts
                if counterpart_section(line) == section
            ]
            target = matching_targets[0] if len(matching_targets) == 1 else None
            add_section_amount(section, target, cash_change, entry_id)
            if section == 'unclassified':
                unclassified_entries.append({
                    'entry_id': entry_id,
                    'entry_date': lines[0]['entry_date'],
                    'description': lines[0]['description'] or '',
                    'amount': cash_change,
                    'reason': reason or 'classification needs review',
                    'account_numbers': [
                        line['account_number'] for line in counterparts
                    ],
                })

        def section_lines(section):
            return [
                line for line in section_activity[section].values()
                if line['amount']
            ]

        direct_operating_cash = sum(
            line['amount'] for line in section_lines('operating')
        )
        preliminary_operating_cash = sum(
            line['amount'] for line in operating_lines
        )
        operating_difference = direct_operating_cash - preliminary_operating_cash
        if operating_difference:
            operating_lines.append({
                'key': 'unresolved_operating_reconciliation',
                'name': 'Unresolved Operating Reconciliation',
                'amount': operating_difference,
            })

        investing_lines = section_lines('investing')
        financing_lines = section_lines('financing')
        unclassified_lines = section_lines('unclassified')
        investing_cash = sum(line['amount'] for line in investing_lines)
        financing_cash = sum(line['amount'] for line in financing_lines)
        unclassified_cash = sum(line['amount'] for line in unclassified_lines)
        computed_cash_change = (
            direct_operating_cash + investing_cash
            + financing_cash + unclassified_cash
        )
        reconciliation_difference = actual_cash_change - computed_cash_change
        ties = (
            reconciliation_difference == 0
            and cash_beginning + computed_cash_change == cash_ending
        )
        operating_reconciled = operating_difference == 0
        classification_complete = not unclassified_entries and bool(cash_rows)

        warnings = []
        if not cash_rows:
            warnings.append(
                "No Cash-subtype accounts were found. Review the chart of accounts."
            )
        if unclassified_entries:
            warnings.append(
                f"{len(unclassified_entries)} cash-affecting entr"
                f"{'y needs' if len(unclassified_entries) == 1 else 'ies need'} "
                "classification review."
            )
        if not operating_reconciled:
            warnings.append(
                "The indirect operating reconciliation has an unresolved difference."
            )
        if noncash_items:
            warnings.append(
                f"{len(noncash_items)} noncash investing or financing entr"
                f"{'y is' if len(noncash_items) == 1 else 'ies are'} disclosed separately."
            )
        if not ties:
            warnings.append("Computed cash movement does not tie to the cash accounts.")

        def money_line(line):
            converted = dict(line)
            converted['amount'] = to_dollars(line['amount'])
            return converted

        return {
            'start_date': start_date,
            'end_date': end_date,
            'operating': {
                'lines': [money_line(line) for line in operating_lines],
                'total': to_dollars(direct_operating_cash),
                'reconciliation_difference': to_dollars(operating_difference),
            },
            'investing': {
                'lines': [money_line(line) for line in investing_lines],
                'total': to_dollars(investing_cash),
            },
            'financing': {
                'lines': [money_line(line) for line in financing_lines],
                'total': to_dollars(financing_cash),
            },
            'unclassified': {
                'lines': [money_line(line) for line in unclassified_lines],
                'entries': [
                    {**entry, 'amount': to_dollars(entry['amount'])}
                    for entry in unclassified_entries
                ],
                'total': to_dollars(unclassified_cash),
            },
            'noncash_items': noncash_items,
            'cash_beginning': to_dollars(cash_beginning),
            'cash_ending': to_dollars(cash_ending),
            'actual_cash_change': to_dollars(actual_cash_change),
            'computed_cash_change': to_dollars(computed_cash_change),
            'reconciliation_difference': to_dollars(reconciliation_difference),
            'ties': ties,
            'operating_reconciled': operating_reconciled,
            'classification_complete': classification_complete,
            'ready': ties and operating_reconciled and classification_complete,
            'warnings': warnings,
        }

    @staticmethod
    def comparative_cash_flow_statement(
        client_id: int,
        start_date: date,
        end_date: date,
    ) -> Dict:
        """Cash flow statement with the same prior-year period alongside it."""
        require_valid_range(start_date, end_date, "Cash flow statement")
        prior_start, prior_end = prior_year_period(start_date, end_date)
        current = ReportGenerator.cash_flow_statement(
            client_id, start_date, end_date
        )
        prior = ReportGenerator.cash_flow_statement(
            client_id, prior_start, prior_end
        )
        available = ReportGenerator._has_history(client_id, prior_end)

        def line_key(line):
            return (
                line.get('key') or '',
                line.get('account_number') or '',
                line.get('name') or '',
            )

        def merge_lines(current_lines, prior_lines):
            current_by_key = {line_key(line): line for line in current_lines}
            prior_by_key = {line_key(line): line for line in prior_lines}
            keys = list(current_by_key)
            keys.extend(key for key in prior_by_key if key not in current_by_key)
            merged = []
            for key in keys:
                current_line = current_by_key.get(key)
                prior_line = prior_by_key.get(key)
                source = current_line or prior_line
                row = {
                    field: source[field]
                    for field in (
                        'key', 'account_id', 'account_number', 'name',
                        'account_ids',
                    )
                    if field in source
                }
                row.update(ReportGenerator._comparison_value(
                    current_line['amount'] if current_line else 0,
                    prior_line['amount'] if prior_line else 0,
                    available,
                ))
                merged.append(row)
            return merged

        def section(name):
            return {
                'lines': merge_lines(
                    current[name]['lines'], prior[name]['lines']
                ),
                'total': ReportGenerator._comparison_value(
                    current[name]['total'], prior[name]['total'], available
                ),
            }

        operating = section('operating')
        operating['reconciliation_difference'] = (
            ReportGenerator._comparison_value(
                current['operating']['reconciliation_difference'],
                prior['operating']['reconciliation_difference'],
                available,
            )
        )
        unclassified = section('unclassified')
        unclassified['current_entries'] = current['unclassified']['entries']
        unclassified['prior_entries'] = (
            prior['unclassified']['entries'] if available else []
        )

        return {
            'current_period': {'start': start_date, 'end': end_date},
            'prior_period': {'start': prior_start, 'end': prior_end},
            'prior_available': available,
            'operating': operating,
            'investing': section('investing'),
            'financing': section('financing'),
            'unclassified': unclassified,
            'cash_beginning': ReportGenerator._comparison_value(
                current['cash_beginning'], prior['cash_beginning'], available
            ),
            'cash_ending': ReportGenerator._comparison_value(
                current['cash_ending'], prior['cash_ending'], available
            ),
            'actual_cash_change': ReportGenerator._comparison_value(
                current['actual_cash_change'], prior['actual_cash_change'], available
            ),
            'computed_cash_change': ReportGenerator._comparison_value(
                current['computed_cash_change'],
                prior['computed_cash_change'], available,
            ),
            'reconciliation_difference': ReportGenerator._comparison_value(
                current['reconciliation_difference'],
                prior['reconciliation_difference'], available,
            ),
            'current_ties': current['ties'],
            'prior_ties': prior['ties'] if available else None,
            'current_operating_reconciled': current['operating_reconciled'],
            'prior_operating_reconciled': (
                prior['operating_reconciled'] if available else None
            ),
            'current_classification_complete': current['classification_complete'],
            'prior_classification_complete': (
                prior['classification_complete'] if available else None
            ),
            'current_ready': current['ready'],
            'prior_ready': prior['ready'] if available else None,
            'current_warnings': current['warnings'],
            'prior_warnings': prior['warnings'] if available else [],
            'current_noncash_items': current['noncash_items'],
            'prior_noncash_items': prior['noncash_items'] if available else [],
        }

    @staticmethod
    def comparative_trial_balance(client_id: int, as_of_date: date) -> Dict:
        """Trial-balance lines at current and prior-year period ends."""
        prior_as_of = prior_year_date(as_of_date)
        current = ReportGenerator.trial_balance(client_id, as_of_date)
        prior = ReportGenerator.trial_balance(client_id, prior_as_of)
        available = ReportGenerator._has_history(client_id, prior_as_of)

        current_by_number = {row.account_number: row for row in current}
        prior_by_number = {row.account_number: row for row in prior}
        account_numbers = list(current_by_number)
        account_numbers.extend(
            number for number in prior_by_number if number not in current_by_number
        )
        merged = []
        for number in account_numbers:
            current_row = current_by_number.get(number)
            prior_row = prior_by_number.get(number)
            source = current_row or prior_row
            merged.append({
                'account_number': number,
                'name': source.account_name,
                'type': source.account_type,
                'current_debit': round(current_row.debit, 2) if current_row else 0.0,
                'current_credit': round(current_row.credit, 2) if current_row else 0.0,
                'prior_debit': (
                    round(prior_row.debit, 2) if available and prior_row else
                    (0.0 if available else None)
                ),
                'prior_credit': (
                    round(prior_row.credit, 2) if available and prior_row else
                    (0.0 if available else None)
                ),
            })

        return {
            'current_as_of': as_of_date,
            'prior_as_of': prior_as_of,
            'prior_available': available,
            'accounts': merged,
            'current_total_debits': round(sum(r.debit for r in current), 2),
            'current_total_credits': round(sum(r.credit for r in current), 2),
            'prior_total_debits': (
                round(sum(r.debit for r in prior), 2) if available else None
            ),
            'prior_total_credits': (
                round(sum(r.credit for r in prior), 2) if available else None
            ),
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
        require_valid_range(start_date, end_date, "General ledger")
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
            is_debit_normal = AccountType.is_debit_normal(account_type)

            entries = []
            running_balance = 0  # integer cents (exact) until converted for output

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
                        balance=to_dollars(running_balance),
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
                    debit=to_dollars(debit),
                    credit=to_dollars(credit),
                    balance=to_dollars(running_balance),
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
    def comparative_trial_balance_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert current/PY trial-balance columns to an exportable table."""
        return pd.DataFrame([
            {
                'Account Number': row['account_number'],
                'Account Name': row['name'],
                'Account Type': row['type'],
                'Current Debit': row['current_debit'] or '',
                'Current Credit': row['current_credit'] or '',
                'PY Debit': ('' if row['prior_debit'] in (None, 0)
                             else row['prior_debit']),
                'PY Credit': ('' if row['prior_credit'] in (None, 0)
                              else row['prior_credit']),
            }
            for row in report['accounts']
        ])

    @staticmethod
    def income_statement_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert a grouped, multi-step income statement for export."""
        rows = []

        revenue_by_key = {
            group['key']: group for group in report['revenue_groups']
        }
        expense_by_key = {
            group['key']: group for group in report['expense_groups']
        }

        def append_group(group):
            rows.append({'Item': f"  {group['group']}", 'Amount': ''})
            for item in group['accounts']:
                rows.append({
                    'Item': f"    {item['account_number']} - {item['name']}",
                    'Amount': item['balance'],
                })
            rows.append({
                'Item': f"  Total {group['group']}",
                'Amount': group['subtotal'],
            })

        rows.append({'Item': 'REVENUE', 'Amount': ''})
        operating_revenue = revenue_by_key.get('operating_revenue')
        if operating_revenue:
            append_group(operating_revenue)
        elif not report['revenues']:
            rows.append({'Item': '  No revenue recorded', 'Amount': ''})

        cogs = expense_by_key.get('cost_of_goods_sold')
        if cogs:
            append_group(cogs)
            rows.append({'Item': 'GROSS PROFIT', 'Amount': report['gross_profit']})

        operating_groups = [
            expense_by_key[key]
            for key in ('operating_expenses', 'depreciation_amortization')
            if key in expense_by_key
        ]
        if operating_groups:
            rows.append({'Item': '', 'Amount': ''})
            rows.append({'Item': 'OPERATING EXPENSES', 'Amount': ''})
            for group in operating_groups:
                append_group(group)
            if operating_revenue or cogs:
                rows.append({
                    'Item': 'OPERATING INCOME',
                    'Amount': report['operating_income'],
                })

        other_groups = []
        for key in ('other_income', 'unclassified'):
            if key in revenue_by_key:
                other_groups.append(revenue_by_key[key])
        for key in ('other_expenses', 'unclassified'):
            if key in expense_by_key:
                other_groups.append(expense_by_key[key])
        if other_groups:
            rows.append({'Item': '', 'Amount': ''})
            rows.append({'Item': 'OTHER AND UNCLASSIFIED', 'Amount': ''})
            for group in other_groups:
                append_group(group)

        rows.extend([
            {'Item': 'Total Revenue', 'Amount': report['total_revenue']},
            {'Item': 'Total Expenses', 'Amount': report['total_expenses']},
            {'Item': '', 'Amount': ''},
        ])
        rows.append({'Item': 'NET INCOME', 'Amount': report['net_income']})

        return pd.DataFrame(rows)

    @staticmethod
    def balance_sheet_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert balance sheet to pandas DataFrame for export."""
        rows = []

        def append_section(title, groups, total_label, total):
            rows.append({'Item': title, 'Amount': ''})
            for group in groups:
                rows.append({'Item': f"  {group['group']}", 'Amount': ''})
                for item in group['accounts']:
                    label = (
                        f"    {item['account_number']} - {item['name']}"
                        if item['account_number'] else f"    {item['name']}"
                    )
                    rows.append({'Item': label, 'Amount': item['balance']})
                rows.append({
                    'Item': f"  Total {group['group']}",
                    'Amount': group['subtotal'],
                })
            rows.append({'Item': total_label, 'Amount': total})

        append_section(
            'ASSETS', report['asset_groups'], 'Total Assets', report['total_assets']
        )
        rows.append({'Item': '', 'Amount': ''})
        append_section(
            'LIABILITIES', report['liability_groups'],
            'Total Liabilities', report['total_liabilities'],
        )
        rows.append({'Item': '', 'Amount': ''})
        append_section(
            'EQUITY', report['equity_groups'], 'Total Equity', report['total_equity']
        )

        rows.append({'Item': '', 'Amount': ''})
        rows.append({'Item': 'TOTAL LIABILITIES & EQUITY', 'Amount': report['total_liabilities_equity']})

        return pd.DataFrame(rows)

    @staticmethod
    def comparative_income_statement_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert a comparative, multi-step income statement for export."""
        rows = []

        revenue_by_key = {
            group['key']: group for group in report['revenue_groups']
        }
        expense_by_key = {
            group['key']: group for group in report['expense_groups']
        }

        def values(item):
            return {
                'Current': item['current'],
                'Prior Year': '' if item['prior'] is None else item['prior'],
                'Change': '' if item['change'] is None else item['change'],
                'Change %': ('' if item['change_percent'] is None
                             else item['change_percent']),
            }

        def append_group(group):
            rows.append({'Item': f"  {group['group']}"})
            for item in group['accounts']:
                rows.append({
                    'Item': f"    {item['account_number']} - {item['name']}",
                    **values(item),
                })
            rows.append({
                'Item': f"  Total {group['group']}",
                **values(group['subtotal']),
            })

        rows.append({'Item': 'REVENUE'})
        operating_revenue = revenue_by_key.get('operating_revenue')
        if operating_revenue:
            append_group(operating_revenue)
        elif not report['revenues']:
            rows.append({'Item': '  No revenue recorded'})

        cogs = expense_by_key.get('cost_of_goods_sold')
        if cogs:
            append_group(cogs)
            rows.append({'Item': 'GROSS PROFIT', **values(report['gross_profit'])})

        operating_groups = [
            expense_by_key[key]
            for key in ('operating_expenses', 'depreciation_amortization')
            if key in expense_by_key
        ]
        if operating_groups:
            rows.extend([{'Item': ''}, {'Item': 'OPERATING EXPENSES'}])
            for group in operating_groups:
                append_group(group)
            if operating_revenue or cogs:
                rows.append({
                    'Item': 'OPERATING INCOME',
                    **values(report['operating_income']),
                })

        other_groups = []
        for key in ('other_income', 'unclassified'):
            if key in revenue_by_key:
                other_groups.append(revenue_by_key[key])
        for key in ('other_expenses', 'unclassified'):
            if key in expense_by_key:
                other_groups.append(expense_by_key[key])
        if other_groups:
            rows.extend([{'Item': ''}, {'Item': 'OTHER AND UNCLASSIFIED'}])
            for group in other_groups:
                append_group(group)

        rows.extend([
            {'Item': 'Total Revenue', **values(report['total_revenue'])},
            {'Item': 'Total Expenses', **values(report['total_expenses'])},
            {'Item': ''},
            {'Item': 'NET INCOME', **values(report['net_income'])},
        ])
        return pd.DataFrame(rows)

    @staticmethod
    def comparative_balance_sheet_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert a comparative balance sheet to an exportable table."""
        rows = []

        def append_section(title, groups, total_label, total):
            rows.append({'Item': title, 'Current': '', 'Prior Year': '',
                         'Change': '', 'Change %': ''})
            for group in groups:
                rows.append({'Item': f"  {group['group']}"})
                for item in group['accounts']:
                    number = item['account_number']
                    rows.append({
                        'Item': f"    {number} - {item['name']}" if number
                                else f"    {item['name']}",
                        'Current': item['current'],
                        'Prior Year': '' if item['prior'] is None else item['prior'],
                        'Change': '' if item['change'] is None else item['change'],
                        'Change %': ('' if item['change_percent'] is None
                                     else item['change_percent']),
                    })
                subtotal = group['subtotal']
                rows.append({
                    'Item': f"  Total {group['group']}",
                    'Current': subtotal['current'],
                    'Prior Year': ('' if subtotal['prior'] is None
                                   else subtotal['prior']),
                    'Change': ('' if subtotal['change'] is None
                               else subtotal['change']),
                    'Change %': ('' if subtotal['change_percent'] is None
                                 else subtotal['change_percent']),
                })
            rows.append({
                'Item': total_label, 'Current': total['current'],
                'Prior Year': '' if total['prior'] is None else total['prior'],
                'Change': '' if total['change'] is None else total['change'],
                'Change %': ('' if total['change_percent'] is None
                             else total['change_percent']),
            })

        append_section('ASSETS', report['asset_groups'], 'Total Assets',
                       report['total_assets'])
        rows.append({'Item': ''})
        append_section('LIABILITIES', report['liability_groups'], 'Total Liabilities',
                       report['total_liabilities'])
        rows.append({'Item': ''})
        append_section('EQUITY', report['equity_groups'], 'Total Equity',
                       report['total_equity'])
        rows.append({'Item': ''})
        total = report['total_liabilities_equity']
        rows.append({
            'Item': 'TOTAL LIABILITIES & EQUITY', 'Current': total['current'],
            'Prior Year': '' if total['prior'] is None else total['prior'],
            'Change': '' if total['change'] is None else total['change'],
            'Change %': ('' if total['change_percent'] is None
                         else total['change_percent']),
        })
        return pd.DataFrame(rows)

    @staticmethod
    def cash_flow_statement_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert cash flow plus its readiness disclosures for export."""
        rows = []

        def append_section(title, section, total_label):
            rows.append({'Item': title, 'Amount': ''})
            for line in section['lines']:
                rows.append({'Item': f"  {line['name']}", 'Amount': line['amount']})
            rows.append({'Item': total_label, 'Amount': section['total']})

        append_section(
            'OPERATING ACTIVITIES', report['operating'],
            'Net Cash Provided by Operating Activities',
        )
        rows.append({'Item': '', 'Amount': ''})
        append_section(
            'INVESTING ACTIVITIES', report['investing'],
            'Net Cash Provided by Investing Activities',
        )
        rows.append({'Item': '', 'Amount': ''})
        append_section(
            'FINANCING ACTIVITIES', report['financing'],
            'Net Cash Provided by Financing Activities',
        )
        unclassified_entries = report['unclassified']['entries']
        if report['unclassified']['lines'] or unclassified_entries:
            rows.append({'Item': '', 'Amount': ''})
            append_section(
                'UNCLASSIFIED CASH ACTIVITY', report['unclassified'],
                'Net Unclassified Cash Activity',
            )
        rows.extend([
            {'Item': '', 'Amount': ''},
            {'Item': 'NET CHANGE IN CASH', 'Amount': report['computed_cash_change']},
            {'Item': 'Cash at Beginning of Period', 'Amount': report['cash_beginning']},
            {'Item': 'CASH AT END OF PERIOD', 'Amount': report['cash_ending']},
            {'Item': 'Reconciliation Difference',
             'Amount': report['reconciliation_difference']},
            {'Item': 'Operating Reconciliation Difference',
             'Amount': report['operating']['reconciliation_difference']},
            {'Item': '', 'Amount': ''},
            {'Item': 'STATUS',
             'Amount': 'READY' if report['ready'] else 'REVIEW WARNINGS'},
            {'Item': 'Cash Tie-Out',
             'Amount': 'PASS' if report['ties'] else 'REVIEW'},
            {'Item': 'Operating Reconciliation',
             'Amount': 'PASS' if report['operating_reconciled'] else 'REVIEW'},
            {'Item': 'Classification',
             'Amount': 'PASS' if report['classification_complete'] else 'REVIEW'},
        ])
        rows.extend(
            {'Item': f"Warning: {warning}", 'Amount': ''}
            for warning in report['warnings']
        )
        if unclassified_entries:
            rows.extend([
                {'Item': '', 'Amount': ''},
                {'Item': 'UNCLASSIFIED ENTRY DETAILS', 'Amount': ''},
            ])
            for entry in unclassified_entries:
                accounts = ', '.join(entry['account_numbers']) or 'none'
                description = entry['description'] or 'No description'
                rows.append({
                    'Item': (
                        f"  {entry['entry_date']} · Entry #{entry['entry_id']} · "
                        f"{entry['reason']} · {description} · Accounts {accounts}"
                    ),
                    'Amount': entry['amount'],
                })
        if report['noncash_items']:
            rows.extend([
                {'Item': '', 'Amount': ''},
                {'Item': 'NONCASH INVESTING AND FINANCING ACTIVITY', 'Amount': ''},
            ])
            for entry in report['noncash_items']:
                rows.append({
                    'Item': (
                        f"  {entry['entry_date']} · Entry #{entry['entry_id']} · "
                        f"{entry['description'] or 'No description'} · "
                        f"Accounts {', '.join(entry['accounts'])}"
                    ),
                    'Amount': '',
                })
        return pd.DataFrame(rows)

    @staticmethod
    def comparative_cash_flow_statement_to_dataframe(report: Dict) -> pd.DataFrame:
        """Convert a current/PY cash flow statement to an exportable table."""
        rows = []

        def values(item):
            return {
                'Current': item['current'],
                'Prior Year': '' if item['prior'] is None else item['prior'],
                'Change': '' if item['change'] is None else item['change'],
                'Change %': ('' if item['change_percent'] is None
                             else item['change_percent']),
            }

        def append_section(title, section, total_label):
            rows.append({'Item': title})
            for line in section['lines']:
                rows.append({'Item': f"  {line['name']}", **values(line)})
            rows.append({'Item': total_label, **values(section['total'])})

        append_section(
            'OPERATING ACTIVITIES', report['operating'],
            'Net Cash Provided by Operating Activities',
        )
        rows.append({'Item': ''})
        append_section(
            'INVESTING ACTIVITIES', report['investing'],
            'Net Cash Provided by Investing Activities',
        )
        rows.append({'Item': ''})
        append_section(
            'FINANCING ACTIVITIES', report['financing'],
            'Net Cash Provided by Financing Activities',
        )
        current_entries = report['unclassified']['current_entries']
        prior_entries = report['unclassified']['prior_entries']
        if report['unclassified']['lines'] or current_entries or prior_entries:
            rows.append({'Item': ''})
            append_section(
                'UNCLASSIFIED CASH ACTIVITY', report['unclassified'],
                'Net Unclassified Cash Activity',
            )
        rows.extend([
            {'Item': ''},
            {'Item': 'NET CHANGE IN CASH', **values(report['computed_cash_change'])},
            {'Item': 'Cash at Beginning of Period', **values(report['cash_beginning'])},
            {'Item': 'CASH AT END OF PERIOD', **values(report['cash_ending'])},
            {'Item': 'Reconciliation Difference',
             **values(report['reconciliation_difference'])},
        ])
        rows.extend([
            {'Item': 'Operating Reconciliation Difference',
             **values(report['operating']['reconciliation_difference'])},
            {'Item': ''},
            {
                'Item': 'STATUS',
                'Current': ('READY' if report['current_ready']
                            else 'REVIEW WARNINGS'),
                'Prior Year': (
                    '' if not report['prior_available'] else
                    ('READY' if report['prior_ready'] else 'REVIEW WARNINGS')
                ),
            },
            {
                'Item': 'Cash Tie-Out',
                'Current': 'PASS' if report['current_ties'] else 'REVIEW',
                'Prior Year': (
                    '' if report['prior_ties'] is None else
                    ('PASS' if report['prior_ties'] else 'REVIEW')
                ),
            },
            {
                'Item': 'Operating Reconciliation',
                'Current': ('PASS' if report['current_operating_reconciled']
                            else 'REVIEW'),
                'Prior Year': (
                    '' if report['prior_operating_reconciled'] is None else
                    ('PASS' if report['prior_operating_reconciled'] else 'REVIEW')
                ),
            },
            {
                'Item': 'Classification',
                'Current': ('PASS' if report['current_classification_complete']
                            else 'REVIEW'),
                'Prior Year': (
                    '' if report['prior_classification_complete'] is None else
                    ('PASS' if report['prior_classification_complete'] else 'REVIEW')
                ),
            },
        ])
        rows.extend(
            {'Item': f"Current warning: {warning}"}
            for warning in report['current_warnings']
        )
        rows.extend(
            {'Item': f"Prior-year warning: {warning}"}
            for warning in report['prior_warnings']
        )

        def append_entry_details(title, entries, amount_column):
            if not entries:
                return
            rows.extend([{'Item': ''}, {'Item': title}])
            for entry in entries:
                accounts = ', '.join(entry['account_numbers']) or 'none'
                description = entry['description'] or 'No description'
                rows.append({
                    'Item': (
                        f"  {entry['entry_date']} · Entry #{entry['entry_id']} · "
                        f"{entry['reason']} · {description} · Accounts {accounts}"
                    ),
                    amount_column: entry['amount'],
                })

        append_entry_details(
            'CURRENT UNCLASSIFIED ENTRY DETAILS', current_entries, 'Current'
        )
        append_entry_details(
            'PRIOR-YEAR UNCLASSIFIED ENTRY DETAILS', prior_entries, 'Prior Year'
        )
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
