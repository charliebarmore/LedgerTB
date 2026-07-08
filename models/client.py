import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from database.connection import get_connection, get_cursor
from database.seed_data import seed_chart_of_accounts_for_client


@dataclass
class Client:
    id: Optional[int] = None
    name: str = ""
    entity_type: Optional[str] = None
    business_type: Optional[str] = None
    fiscal_year_end_month: int = 12
    is_active: bool = True
    # Extended client info (migration 003)
    tax_id: Optional[str] = None            # EIN / SSN
    dba_name: Optional[str] = None          # "doing business as" / trade name
    address_line1: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

    @staticmethod
    def _from_row(row) -> 'Client':
        """Build a Client from a DB row (single source of the row mapping)."""
        def g(key):
            return row[key] if key in row.keys() else None
        return Client(
            id=row['id'],
            name=row['name'],
            entity_type=row['entity_type'],
            business_type=g('business_type'),
            fiscal_year_end_month=row['fiscal_year_end_month'],
            is_active=bool(row['is_active']),
            tax_id=g('tax_id'),
            dba_name=g('dba_name'),
            address_line1=g('address_line1'),
            address_city=g('address_city'),
            address_state=g('address_state'),
            address_zip=g('address_zip'),
            contact_name=g('contact_name'),
            contact_email=g('contact_email'),
            contact_phone=g('contact_phone'),
            notes=g('notes'),
        )

    @staticmethod
    def get_all(active_only: bool = True) -> List['Client']:
        """Get all clients, optionally filtered by active status."""
        with get_cursor() as cursor:
            if active_only:
                cursor.execute("SELECT * FROM clients WHERE is_active = 1 ORDER BY name")
            else:
                cursor.execute("SELECT * FROM clients ORDER BY name")
            rows = cursor.fetchall()
        return [Client._from_row(row) for row in rows]

    @staticmethod
    def get_by_id(client_id: int) -> Optional['Client']:
        """Get a client by its ID."""
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
            row = cursor.fetchone()
        return Client._from_row(row) if row else None

    def save(self, seed_accounts: bool = True) -> int:
        """
        Save or update the client.

        Args:
            seed_accounts: If True and this is a new client, seed default chart of accounts
        """
        from models.audit_log import AuditLog

        # Kept on a raw connection (with try/finally for leak safety) because the
        # account seeder writes on the same connection/transaction as the insert.
        conn = get_connection()
        is_new = self.id is None
        old_values = None
        try:
            cursor = conn.cursor()

            extended = (
                self.tax_id, self.dba_name, self.address_line1, self.address_city,
                self.address_state, self.address_zip, self.contact_name,
                self.contact_email, self.contact_phone, self.notes,
            )
            if is_new:
                cursor.execute(
                    """
                    INSERT INTO clients
                        (name, entity_type, business_type, fiscal_year_end_month, is_active,
                         tax_id, dba_name, address_line1, address_city, address_state,
                         address_zip, contact_name, contact_email, contact_phone, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.name, self.entity_type, self.business_type,
                     self.fiscal_year_end_month, int(self.is_active)) + extended
                )
                self.id = cursor.lastrowid
            else:
                cursor.execute("SELECT name, entity_type, business_type, fiscal_year_end_month, is_active FROM clients WHERE id = ?", (self.id,))
                prev = cursor.fetchone()
                if prev:
                    old_values = {
                        'name': prev['name'],
                        'entity_type': prev['entity_type'],
                        'business_type': prev['business_type'] if 'business_type' in prev.keys() else None,
                        'fiscal_year_end_month': prev['fiscal_year_end_month'],
                        'is_active': bool(prev['is_active']),
                    }
                cursor.execute(
                    """
                    UPDATE clients
                    SET name = ?, entity_type = ?, business_type = ?, fiscal_year_end_month = ?, is_active = ?,
                        tax_id = ?, dba_name = ?, address_line1 = ?, address_city = ?, address_state = ?,
                        address_zip = ?, contact_name = ?, contact_email = ?, contact_phone = ?, notes = ?
                    WHERE id = ?
                    """,
                    (self.name, self.entity_type, self.business_type,
                     self.fiscal_year_end_month, int(self.is_active)) + extended + (self.id,)
                )

            conn.commit()

            # Seed chart of accounts for new clients based on entity and business type
            if is_new and seed_accounts:
                seed_chart_of_accounts_for_client(
                    conn,
                    self.id,
                    self.entity_type or "Sole Proprietorship",
                    self.business_type or "Other"
                )
        finally:
            conn.close()

        new_values = {
            'name': self.name,
            'entity_type': self.entity_type,
            'business_type': self.business_type,
            'fiscal_year_end_month': self.fiscal_year_end_month,
            'is_active': self.is_active,
        }
        AuditLog.log_change_safe(
            client_id=self.id,
            table_name='clients',
            record_id=self.id,
            action='INSERT' if is_new else 'UPDATE',
            old_values=old_values,
            new_values=new_values,
        )
        return self.id

    def deactivate(self):
        """Soft delete - mark client as inactive."""
        self.is_active = False
        self.save(seed_accounts=False)

    @staticmethod
    def has_transactions(client_id: int) -> bool:
        """Check if a client has any journal entries."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE client_id = ?",
                (client_id,)
            )
            return cursor.fetchone()[0] > 0

    @staticmethod
    def get_first() -> Optional['Client']:
        """Get the first available client (for default selection)."""
        clients = Client.get_all(active_only=True)
        return clients[0] if clients else None
