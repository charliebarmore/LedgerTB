from dataclasses import dataclass
from datetime import datetime
import json
from typing import Optional

from database.connection import get_cursor


AMOUNT_FORMAT_SINGLE = "single"
AMOUNT_FORMAT_SEPARATE = "separate"
AMOUNT_FORMATS = {AMOUNT_FORMAT_SINGLE, AMOUNT_FORMAT_SEPARATE}
SIGN_CONVENTIONS = {"bank", "credit_card", "flip"}


@dataclass
class ImportProfile:
    client_id: int
    bank_account_id: int
    name: str
    date_column: str
    description_column: str
    amount_format: str
    sign_convention: str
    amount_column: Optional[str] = None
    debit_column: Optional[str] = None
    credit_column: Optional[str] = None
    header_signature: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @staticmethod
    def _from_row(row) -> "ImportProfile":
        return ImportProfile(
            id=row["id"],
            client_id=row["client_id"],
            bank_account_id=row["bank_account_id"],
            name=row["name"],
            date_column=row["date_column"],
            description_column=row["description_column"],
            amount_format=row["amount_format"],
            amount_column=row["amount_column"],
            debit_column=row["debit_column"],
            credit_column=row["credit_column"],
            header_signature=row["header_signature"],
            sign_convention=row["sign_convention"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def validate(self) -> None:
        self.name = (self.name or "").strip()
        self.date_column = (self.date_column or "").strip()
        self.description_column = (self.description_column or "").strip()
        self.amount_column = (self.amount_column or "").strip() or None
        self.debit_column = (self.debit_column or "").strip() or None
        self.credit_column = (self.credit_column or "").strip() or None
        if not self.client_id or not self.bank_account_id:
            raise ValueError("Client and bank account are required.")
        if not self.name:
            raise ValueError("A format name is required.")
        if len(self.name) > 80:
            raise ValueError("Format names must be 80 characters or fewer.")
        if not self.date_column or not self.description_column:
            raise ValueError("Date and description columns are required.")
        if self.amount_format not in AMOUNT_FORMATS:
            raise ValueError("Amount format must be single or separate.")
        if self.sign_convention not in SIGN_CONVENTIONS:
            raise ValueError("Sign convention is invalid.")
        if self.amount_format == AMOUNT_FORMAT_SINGLE:
            if not self.amount_column:
                raise ValueError("An amount column is required for single-column profiles.")
            self.debit_column = None
            self.credit_column = None
        else:
            if not self.debit_column and not self.credit_column:
                raise ValueError(
                    "A debit or credit column is required for separate-column profiles."
                )
            self.amount_column = None

    @staticmethod
    def signature_for_columns(columns: list[str]) -> str:
        """Return a stable, order-sensitive signature for an export's headers."""
        return json.dumps([str(column) for column in columns], separators=(",", ":"))

    def save(self) -> int:
        """Create a format or update this exact profile atomically."""
        from models.audit_log import AuditLog

        self.validate()
        with get_cursor(commit=True) as cursor:
            old = None
            if self.id is not None:
                cursor.execute(
                    """
                    SELECT * FROM import_profiles
                    WHERE id = ? AND client_id = ? AND bank_account_id = ?
                    """,
                    (self.id, self.client_id, self.bank_account_id),
                )
                old = cursor.fetchone()
                if not old:
                    raise ValueError("The saved import format no longer exists.")
            cursor.execute(
                """
                SELECT type FROM accounts
                WHERE id = ? AND client_id = ? AND is_active = 1
                """,
                (self.bank_account_id, self.client_id),
            )
            account = cursor.fetchone()
            if not account:
                raise ValueError("The import account must be active and belong to the client.")
            if account["type"] not in {"Asset", "Liability"}:
                raise ValueError("Import profiles require an asset or liability account.")

            cursor.execute(
                """
                SELECT id FROM import_profiles
                WHERE client_id = ? AND bank_account_id = ?
                  AND name = ? COLLATE NOCASE AND id != COALESCE(?, -1)
                """,
                (self.client_id, self.bank_account_id, self.name, self.id),
            )
            if cursor.fetchone():
                raise ValueError(
                    f'A saved format named "{self.name}" already exists for this account.'
                )

            values = (
                self.name,
                self.date_column,
                self.description_column,
                self.amount_format,
                self.amount_column,
                self.debit_column,
                self.credit_column,
                self.sign_convention,
                self.header_signature,
            )
            if old:
                cursor.execute(
                    """
                    UPDATE import_profiles
                    SET name = ?, date_column = ?, description_column = ?, amount_format = ?,
                        amount_column = ?, debit_column = ?, credit_column = ?,
                        sign_convention = ?, header_signature = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND client_id = ?
                    """,
                    (*values, old["id"], self.client_id),
                )
                self.id = old["id"]
                action = "UPDATE"
                old_values = {
                    "name": old["name"],
                    "date_column": old["date_column"],
                    "description_column": old["description_column"],
                    "amount_format": old["amount_format"],
                    "amount_column": old["amount_column"],
                    "debit_column": old["debit_column"],
                    "credit_column": old["credit_column"],
                    "sign_convention": old["sign_convention"],
                    "header_signature": old["header_signature"],
                }
            else:
                cursor.execute(
                    """
                    INSERT INTO import_profiles
                        (client_id, bank_account_id, name, date_column, description_column,
                         amount_format, amount_column, debit_column, credit_column,
                         sign_convention, header_signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.client_id, self.bank_account_id, *values),
                )
                self.id = cursor.lastrowid
                action = "INSERT"
                old_values = None

            AuditLog.write(
                cursor,
                self.client_id,
                "import_profiles",
                self.id,
                action,
                old_values=old_values,
                new_values={
                    "bank_account_id": self.bank_account_id,
                    "name": self.name,
                    "date_column": self.date_column,
                    "description_column": self.description_column,
                    "amount_format": self.amount_format,
                    "amount_column": self.amount_column,
                    "debit_column": self.debit_column,
                    "credit_column": self.credit_column,
                    "sign_convention": self.sign_convention,
                    "header_signature": self.header_signature,
                },
            )
        saved = self.get_by_id(self.client_id, self.id)
        self.created_at = saved.created_at
        self.updated_at = saved.updated_at
        return self.id

    @staticmethod
    def get_by_id(client_id: int, profile_id: int) -> Optional["ImportProfile"]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT ip.*
                FROM import_profiles ip
                JOIN accounts a ON a.id = ip.bank_account_id
                WHERE ip.client_id = ? AND ip.id = ?
                  AND a.client_id = ip.client_id
                """,
                (client_id, profile_id),
            )
            row = cursor.fetchone()
        return ImportProfile._from_row(row) if row else None

    @staticmethod
    def list_for_account(client_id: int, bank_account_id: int) -> list["ImportProfile"]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT ip.*
                FROM import_profiles ip
                JOIN accounts a ON a.id = ip.bank_account_id
                WHERE ip.client_id = ? AND ip.bank_account_id = ?
                  AND a.client_id = ip.client_id
                ORDER BY ip.name COLLATE NOCASE, ip.id
                """,
                (client_id, bank_account_id),
            )
            rows = cursor.fetchall()
        return [ImportProfile._from_row(row) for row in rows]

    @staticmethod
    def match_for_columns(
        profiles: list["ImportProfile"], columns: list[str]
    ) -> Optional["ImportProfile"]:
        """Prefer an exact header match; otherwise accept one unambiguous legacy match."""
        signature = ImportProfile.signature_for_columns(columns)
        exact = [profile for profile in profiles if profile.header_signature == signature]
        if len(exact) == 1:
            return exact[0]

        compatible = [
            profile
            for profile in profiles
            if not profile.resolve_columns(columns, {})["missing"]
        ]
        return compatible[0] if len(compatible) == 1 else None

    @staticmethod
    def delete(client_id: int, profile_id: int) -> bool:
        from models.audit_log import AuditLog

        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "SELECT * FROM import_profiles WHERE client_id = ? AND id = ?",
                (client_id, profile_id),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute(
                "DELETE FROM import_profiles WHERE id = ? AND client_id = ?",
                (row["id"], client_id),
            )
            AuditLog.write(
                cursor,
                client_id,
                "import_profiles",
                row["id"],
                "DELETE",
                old_values={
                    "bank_account_id": row["bank_account_id"],
                    "name": row["name"],
                    "date_column": row["date_column"],
                    "description_column": row["description_column"],
                    "amount_format": row["amount_format"],
                    "amount_column": row["amount_column"],
                    "debit_column": row["debit_column"],
                    "credit_column": row["credit_column"],
                    "sign_convention": row["sign_convention"],
                    "header_signature": row["header_signature"],
                },
            )
        return True

    def resolve_columns(self, columns: list[str], detected: dict) -> dict:
        """Return strict saved defaults, or safe auto-detection if incompatible."""
        available = set(columns)
        required = [self.date_column, self.description_column]
        if self.amount_format == AMOUNT_FORMAT_SINGLE:
            required.append(self.amount_column)
        else:
            required.extend(
                column for column in (self.debit_column, self.credit_column) if column
            )
        missing = [column for column in required if column not in available]
        if not missing:
            return {
                "applied": True,
                "missing": [],
                "date_column": self.date_column,
                "description_column": self.description_column,
                "amount_format": self.amount_format,
                "amount_column": self.amount_column,
                "debit_column": self.debit_column,
                "credit_column": self.credit_column,
            }
        return {
            "applied": False,
            "missing": missing,
            "date_column": detected.get("date") or columns[0],
            "description_column": detected.get("description") or columns[0],
            "amount_format": (
                AMOUNT_FORMAT_SINGLE
                if detected.get("amount")
                else AMOUNT_FORMAT_SEPARATE
            ),
            "amount_column": detected.get("amount"),
            "debit_column": detected.get("debit"),
            "credit_column": detected.get("credit"),
        }
