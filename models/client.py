import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from database.connection import get_connection
from database.seed_data import seed_chart_of_accounts_for_client


@dataclass
class Client:
    id: Optional[int] = None
    name: str = ""
    entity_type: Optional[str] = None
    business_type: Optional[str] = None
    fiscal_year_end_month: int = 12
    is_active: bool = True

    @staticmethod
    def get_all(active_only: bool = True) -> List['Client']:
        """Get all clients, optionally filtered by active status."""
        conn = get_connection()
        cursor = conn.cursor()

        if active_only:
            cursor.execute(
                "SELECT * FROM clients WHERE is_active = 1 ORDER BY name"
            )
        else:
            cursor.execute("SELECT * FROM clients ORDER BY name")

        rows = cursor.fetchall()
        conn.close()

        return [Client(
            id=row['id'],
            name=row['name'],
            entity_type=row['entity_type'],
            business_type=row['business_type'] if 'business_type' in row.keys() else None,
            fiscal_year_end_month=row['fiscal_year_end_month'],
            is_active=bool(row['is_active'])
        ) for row in rows]

    @staticmethod
    def get_by_id(client_id: int) -> Optional['Client']:
        """Get a client by its ID."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Client(
                id=row['id'],
                name=row['name'],
                entity_type=row['entity_type'],
                business_type=row['business_type'] if 'business_type' in row.keys() else None,
                fiscal_year_end_month=row['fiscal_year_end_month'],
                is_active=bool(row['is_active'])
            )
        return None

    def save(self, seed_accounts: bool = True) -> int:
        """
        Save or update the client.

        Args:
            seed_accounts: If True and this is a new client, seed default chart of accounts
        """
        conn = get_connection()
        cursor = conn.cursor()

        is_new = self.id is None

        if is_new:
            cursor.execute(
                """
                INSERT INTO clients (name, entity_type, business_type, fiscal_year_end_month, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.name, self.entity_type, self.business_type, self.fiscal_year_end_month, int(self.is_active))
            )
            self.id = cursor.lastrowid
        else:
            cursor.execute(
                """
                UPDATE clients
                SET name = ?, entity_type = ?, business_type = ?, fiscal_year_end_month = ?, is_active = ?
                WHERE id = ?
                """,
                (self.name, self.entity_type, self.business_type, self.fiscal_year_end_month, int(self.is_active), self.id)
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

        conn.close()
        return self.id

    def deactivate(self):
        """Soft delete - mark client as inactive."""
        self.is_active = False
        self.save(seed_accounts=False)

    @staticmethod
    def has_transactions(client_id: int) -> bool:
        """Check if a client has any journal entries."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM journal_entries WHERE client_id = ?",
            (client_id,)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    @staticmethod
    def get_first() -> Optional['Client']:
        """Get the first available client (for default selection)."""
        clients = Client.get_all(active_only=True)
        return clients[0] if clients else None
