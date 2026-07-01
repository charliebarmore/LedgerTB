import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from io import StringIO
import uuid


class CSVImporter:
    """Handles importing and parsing bank CSV files."""

    # Common date formats to try
    DATE_FORMATS = [
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%m/%d/%y",
        "%d-%m-%Y",
    ]

    # Common column name mappings
    COLUMN_MAPPINGS = {
        'date': ['date', 'transaction date', 'trans date', 'posting date', 'post date', 'txn date'],
        'description': ['description', 'memo', 'name', 'payee', 'merchant', 'transaction description', 'details'],
        'amount': ['amount', 'transaction amount', 'amt'],
        'debit': ['debit', 'withdrawal', 'withdrawals', 'debit amount', 'money out'],
        'credit': ['credit', 'deposit', 'deposits', 'credit amount', 'money in'],
    }

    @staticmethod
    def preview_csv(file_content: str, num_rows: int = 10, header_row: int = 0) -> Tuple[pd.DataFrame, List[str]]:
        """
        Preview a CSV file and return sample data with column names.

        Args:
            file_content: The CSV file content as string
            num_rows: Number of rows to preview
            header_row: Which row contains the column headers (0-indexed)

        Returns:
            Tuple of (DataFrame with sample rows, list of column names)
        """
        df = pd.read_csv(StringIO(file_content), header=header_row)
        return df.head(num_rows), list(df.columns)

    @staticmethod
    def detect_columns(columns: List[str]) -> Dict[str, Optional[str]]:
        """
        Auto-detect which columns map to date, description, and amount fields.

        Returns:
            Dict mapping field names to detected column names
        """
        detected = {
            'date': None,
            'description': None,
            'amount': None,
            'debit': None,
            'credit': None,
        }

        columns_lower = [c.lower().strip() for c in columns]

        for field, possible_names in CSVImporter.COLUMN_MAPPINGS.items():
            for col, col_lower in zip(columns, columns_lower):
                if col_lower in possible_names:
                    detected[field] = col
                    break

        return detected

    @staticmethod
    def parse_date(date_str: str) -> Optional[datetime]:
        """Try to parse a date string using common formats."""
        if pd.isna(date_str):
            return None

        date_str = str(date_str).strip()

        for fmt in CSVImporter.DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        return None

    @staticmethod
    def parse_csv(
        file_content: str,
        date_column: str,
        description_column: str,
        amount_column: Optional[str] = None,
        debit_column: Optional[str] = None,
        credit_column: Optional[str] = None,
        source_account_column: Optional[str] = None,
        header_row: int = 0
    ) -> List[Dict]:
        """
        Parse a CSV file into a list of transaction dictionaries.

        Args:
            file_content: The CSV file content as string
            date_column: Name of the date column
            description_column: Name of the description column
            amount_column: Name of single amount column (positive = deposit, negative = withdrawal)
            debit_column: Name of debit/withdrawal column (if separate columns)
            credit_column: Name of credit/deposit column (if separate columns)
            source_account_column: Name of column identifying source account (for multi-account imports)
            header_row: Which row contains the column headers (0-indexed)

        Returns:
            List of dicts with keys: date, description, amount, batch_id, and optionally source_account
        """
        df = pd.read_csv(StringIO(file_content), header=header_row)

        transactions = []
        batch_id = str(uuid.uuid4())[:8]

        for _, row in df.iterrows():
            # Parse date
            parsed_date = CSVImporter.parse_date(row[date_column])
            if not parsed_date:
                continue  # Skip rows with invalid dates

            # Get description
            description = str(row[description_column]).strip() if pd.notna(row[description_column]) else ""

            # Calculate amount
            if amount_column:
                # Single amount column
                try:
                    amount_str = str(row[amount_column]).replace(',', '').replace('$', '').strip()
                    # Handle parentheses for negative numbers: (100.00) -> -100.00
                    if amount_str.startswith('(') and amount_str.endswith(')'):
                        amount = -abs(float(amount_str[1:-1]))
                    # Handle trailing minus sign: 100.00- -> -100.00
                    elif amount_str.endswith('-'):
                        amount = -abs(float(amount_str[:-1]))
                    else:
                        amount = float(amount_str)
                except (ValueError, TypeError):
                    continue
            else:
                # Separate debit/credit columns
                debit = 0.0
                credit = 0.0

                if debit_column and pd.notna(row.get(debit_column)):
                    try:
                        debit = abs(float(str(row[debit_column]).replace(',', '').replace('$', '').replace('(', '').replace(')', '')))
                    except (ValueError, TypeError):
                        pass

                if credit_column and pd.notna(row.get(credit_column)):
                    try:
                        credit = abs(float(str(row[credit_column]).replace(',', '').replace('$', '').replace('(', '').replace(')', '')))
                    except (ValueError, TypeError):
                        pass

                # Deposits are positive, withdrawals are negative
                amount = credit - debit

            transaction = {
                'date': parsed_date.date(),
                'description': description,
                'amount': amount,
                'batch_id': batch_id
            }

            # Add source account if column specified
            if source_account_column and source_account_column in row.index:
                transaction['source_account'] = str(row[source_account_column]).strip() if pd.notna(row[source_account_column]) else ""

            transactions.append(transaction)

        return transactions

    @staticmethod
    def normalize_description(description: str) -> str:
        """
        Normalize a transaction description for pattern matching.
        Removes common prefixes, numbers, and standardizes format.
        """
        import re

        text = description.upper().strip()

        # Remove common prefixes
        prefixes_to_remove = [
            'POS ', 'POS PURCHASE ', 'DEBIT CARD ', 'VISA ', 'MASTERCARD ',
            'CHECK CARD ', 'ACH ', 'ELECTRONIC ', 'WIRE ', 'TRANSFER ',
            'ONLINE ', 'MOBILE ', 'ATM ', 'CASH '
        ]

        for prefix in prefixes_to_remove:
            if text.startswith(prefix):
                text = text[len(prefix):]

        # Remove dates (various formats)
        text = re.sub(r'\d{1,2}/\d{1,2}(/\d{2,4})?', '', text)
        text = re.sub(r'\d{1,2}-\d{1,2}(-\d{2,4})?', '', text)

        # Remove transaction IDs and reference numbers
        text = re.sub(r'#\d+', '', text)
        text = re.sub(r'REF\s*#?\s*\d+', '', text)
        text = re.sub(r'TRACE\s*#?\s*\d+', '', text)

        # Remove card numbers (last 4 digits patterns)
        text = re.sub(r'\*+\d{4}', '', text)
        text = re.sub(r'X+\d{4}', '', text)

        # Remove extra whitespace
        text = ' '.join(text.split())

        return text
