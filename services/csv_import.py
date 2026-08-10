import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from io import StringIO
import uuid


# How a statement's amount signs should be read. Shared by the CSV and the
# PDF/image statement importers so both offer the same choices.
SIGN_CONVENTIONS = {
    "bank": "Bank Account (negative = expense, positive = deposit)",
    "credit_card": "Credit Card (positive = expense, negative = payment/credit)",
    "flip": "Flip All Signs (reverse the default interpretation)",
}


# A real bank statement is kilobytes. These bounds exist because the parse
# runs the moment a file is chosen, before any confirmation: a one-line file
# of a million empty columns costs pandas minutes and hundreds of megabytes,
# which in a single-process desktop app means the window simply stops
# responding. document_import.py has had bounds like these all along.
MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_CSV_COLUMNS = 512


class CsvTooLarge(ValueError):
    """An upload outside the bounds a bank statement could plausibly have."""


class AmbiguousAmount(ValueError):
    """An amount whose decimal separator cannot be determined."""


def _reject_absurd_shape(file_content: str, header_row: int = 0) -> None:
    """Check the header line BEFORE handing the file to pandas.

    Checking the parsed DataFrame is too late: the cost being guarded against
    is the parse itself, which is quadratic in column count and takes minutes
    on a file with a million of them.
    """
    lines = file_content.split("\n", header_row + 1)
    if len(lines) <= header_row:
        return
    columns = lines[header_row].count(",") + 1
    if columns > MAX_CSV_COLUMNS:
        raise CsvTooLarge(
            f"That file has about {columns:,} columns. A bank statement has a "
            "handful — this looks like the wrong file, or one saved in an "
            "unexpected format."
        )


def parse_amount(raw) -> float:
    """Parse one amount cell, refusing anything genuinely ambiguous.

    Stripping commas unconditionally is wrong outside US formatting: a
    European statement writing 1,23 for one euro twenty-three became 123.00,
    and 1.234,56 became 1.23. Both legs of the entry took the same wrong
    figure, so the entry balanced, the trial balance tied, and every integrity
    check passed — a hundredfold error with nothing anywhere to catch it.
    Refusing the row surfaces the problem; guessing buries it.
    """
    text = str(raw).strip().replace("$", "").replace(" ", "").replace(" ", "")
    if not text:
        raise ValueError("empty amount")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    elif text.endswith("-"):
        negative, text = True, text[:-1]
    elif text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]

    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # Whichever separator comes last is the decimal point.
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        _, _, after = text.rpartition(",")
        # A trailing group of exactly three digits is a thousands separator
        # ("1,234" is a thousand-odd dollars on every US statement). One or two
        # digits cannot be — "1,23" is a European decimal, and reading it as
        # US formatting was the hundredfold error.
        text = text.replace(",", "") if len(after) == 3 else text.replace(",", ".")

    value = float(text)
    return -abs(value) if negative else value


def apply_sign_convention(amount: float, sign_convention: str) -> float:
    """Normalize a statement amount so negative means money out.

    Bank statements already read that way. Credit-card statements print
    purchases positive, and "flip" exists for exports that are backwards, so
    both are negated. Keeping this in one function means the totals previewed
    before an import cannot disagree with what actually posts.
    """
    return -amount if sign_convention in ("credit_card", "flip") else amount


def summarize_import_amounts(amounts, sign_convention: str,
                             account_type: Optional[str] = None) -> Dict:
    """Directional totals for a batch about to be imported.

    A min-to-max range says almost nothing about a statement — a file of
    identical charges reports "79.00 to 79.00". What a reader wants is how much
    went out, how much came in, and the net, in the language of the account:
    charges and payments for a card, disbursements and receipts for a bank.

    Amounts are normalized first, so the totals describe what will be posted
    rather than how the file happened to be written.
    """
    normalized = [apply_sign_convention(amount, sign_convention) for amount in amounts]
    inflow = sum(amount for amount in normalized if amount > 0)
    outflow = -sum(amount for amount in normalized if amount < 0)

    if account_type == "Liability":
        outflow_label, inflow_label = "Total charges", "Total payments"
    else:
        outflow_label, inflow_label = "Total disbursements", "Total receipts"

    return {
        "outflow_label": outflow_label,
        "outflow": round(outflow, 2),
        "inflow_label": inflow_label,
        "inflow": round(inflow, 2),
        "net": round(inflow - outflow, 2),
    }


def default_sign_convention(account_type: Optional[str]) -> str:
    """The convention a statement for this kind of account normally uses.

    Liability accounts are credit cards and loans, whose statements print
    purchases positive and payments negative. Assets are bank and cash
    accounts, where withdrawals are negative. Anything else falls back to the
    bank convention, which is also the safer guess: it leaves amounts as the
    file states them rather than inverting every row.
    """
    return "credit_card" if account_type == "Liability" else "bank"


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
    def decode_upload(content: bytes) -> str:
        """Decode common bank-export encodings into text.

        Never raises on odd bytes: a statement that will not decode should say
        so in the page, not surface a UnicodeDecodeError traceback.
        """
        if len(content) > MAX_CSV_BYTES:
            raise CsvTooLarge(
                f"That file is {len(content) / 1_048_576:.0f} MB. Statement "
                f"exports are normally well under {MAX_CSV_BYTES // 1_048_576} "
                "MB — check you picked the right file."
            )
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                return content.decode("utf-16")
            except UnicodeDecodeError:
                pass
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Many older financial exports use Windows-1252 for smart quotes
            # and accented merchant names.
            return content.decode("cp1252", errors="replace")

    @staticmethod
    def preview_csv(file_content: str, num_rows: Optional[int] = 10, header_row: int = 0) -> Tuple[pd.DataFrame, List[str]]:
        """
        Preview a CSV file and return sample data with column names.

        Args:
            file_content: The CSV file content as string
            num_rows: Number of rows to preview; ``None`` returns every row.
                Callers that show the user what they are about to import should
                pass None — a truncated table reads as though the file itself
                were short.
            header_row: Which row contains the column headers (0-indexed)

        Returns:
            Tuple of (DataFrame with sample rows, list of column names)
        """
        _reject_absurd_shape(file_content, header_row)
        df = pd.read_csv(StringIO(file_content), header=header_row)
        return (df if num_rows is None else df.head(num_rows)), list(df.columns)

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
        header_row: int = 0,
        source_id: Optional[str] = None,
        source_filename: Optional[str] = None,
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
        _reject_absurd_shape(file_content, header_row)
        df = pd.read_csv(StringIO(file_content), header=header_row)

        transactions = []
        batch_id = str(uuid.uuid4())[:8]

        for row_position, (_, row) in enumerate(df.iterrows(), start=1):
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
                    amount = parse_amount(row[amount_column])
                except (ValueError, TypeError):
                    continue
            else:
                # Separate debit/credit columns
                debit = 0.0
                credit = 0.0

                if debit_column and pd.notna(row.get(debit_column)):
                    try:
                        debit = abs(parse_amount(row[debit_column]))
                    except (ValueError, TypeError):
                        pass

                if credit_column and pd.notna(row.get(credit_column)):
                    try:
                        credit = abs(parse_amount(row[credit_column]))
                    except (ValueError, TypeError):
                        pass

                # Deposits are positive, withdrawals are negative
                amount = credit - debit

            transaction = {
                'date': parsed_date.date(),
                'description': description,
                'amount': amount,
                'batch_id': batch_id,
                # Human-friendly physical CSV line number (header is line 1).
                'source_row_number': header_row + row_position + 1,
                'source_id': source_id,
                'source_filename': source_filename,
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
