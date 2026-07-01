import sqlite3
import re
from typing import Optional, List, Dict
from database.connection import get_connection
from services.csv_import import CSVImporter


class PatternLearner:
    """Learns and applies vendor-to-account mappings per client."""

    @staticmethod
    def learn_pattern(client_id: int, description: str, account_id: int):
        """
        Learn a pattern from a transaction description for a specific client.
        Stores normalized description as a pattern for future matching.
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Normalize the description
        normalized = CSVImporter.normalize_description(description)

        if not normalized:
            conn.close()
            return

        # Check if this pattern already exists for this client
        cursor.execute(
            "SELECT id, times_used FROM categorization_rules WHERE client_id = ? AND pattern = ?",
            (client_id, normalized)
        )
        existing = cursor.fetchone()

        if existing:
            # Update existing rule
            cursor.execute(
                """
                UPDATE categorization_rules
                SET default_account_id = ?, times_used = times_used + 1
                WHERE id = ?
                """,
                (account_id, existing['id'])
            )
        else:
            # Create new rule
            cursor.execute(
                """
                INSERT INTO categorization_rules (client_id, pattern, default_account_id, confidence, times_used)
                VALUES (?, ?, ?, 1.0, 1)
                """,
                (client_id, normalized, account_id)
            )

        conn.commit()
        conn.close()

    @staticmethod
    def find_match(client_id: int, description: str) -> Optional[Dict]:
        """
        Find a matching pattern for a transaction description within a client.

        Returns:
            Dict with 'account_id', 'confidence', 'pattern' or None if no match
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Normalize the description
        normalized = CSVImporter.normalize_description(description)

        if not normalized:
            conn.close()
            return None

        # Try exact match first
        cursor.execute(
            """
            SELECT cr.*, a.name as account_name, a.account_number
            FROM categorization_rules cr
            JOIN accounts a ON cr.default_account_id = a.id
            WHERE cr.client_id = ? AND cr.pattern = ?
            ORDER BY cr.times_used DESC
            LIMIT 1
            """,
            (client_id, normalized)
        )
        exact = cursor.fetchone()

        if exact:
            conn.close()
            return {
                'account_id': exact['default_account_id'],
                'account_name': exact['account_name'],
                'account_number': exact['account_number'],
                'confidence': exact['confidence'],
                'pattern': exact['pattern'],
                'match_type': 'exact'
            }

        # Try partial match (pattern contained in description or vice versa)
        cursor.execute(
            """
            SELECT cr.*, a.name as account_name, a.account_number
            FROM categorization_rules cr
            JOIN accounts a ON cr.default_account_id = a.id
            WHERE cr.client_id = ?
            ORDER BY cr.times_used DESC
            """,
            (client_id,)
        )

        for rule in cursor.fetchall():
            pattern = rule['pattern']
            # Check if pattern is in normalized description
            if pattern in normalized or normalized in pattern:
                conn.close()
                return {
                    'account_id': rule['default_account_id'],
                    'account_name': rule['account_name'],
                    'account_number': rule['account_number'],
                    'confidence': rule['confidence'] * 0.8,  # Lower confidence for partial match
                    'pattern': pattern,
                    'match_type': 'partial'
                }

            # Check word-level matching
            pattern_words = set(pattern.split())
            desc_words = set(normalized.split())
            common_words = pattern_words & desc_words

            # If significant word overlap, consider it a match
            if len(common_words) >= 2 and len(common_words) / len(pattern_words) > 0.5:
                conn.close()
                return {
                    'account_id': rule['default_account_id'],
                    'account_name': rule['account_name'],
                    'account_number': rule['account_number'],
                    'confidence': rule['confidence'] * 0.6,  # Even lower for word match
                    'pattern': pattern,
                    'match_type': 'word'
                }

        conn.close()
        return None

    @staticmethod
    def get_all_rules(client_id: int) -> List[Dict]:
        """Get all categorization rules for a client with account info."""
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT cr.*, a.name as account_name, a.account_number
            FROM categorization_rules cr
            JOIN accounts a ON cr.default_account_id = a.id
            WHERE cr.client_id = ?
            ORDER BY cr.times_used DESC
            """,
            (client_id,)
        )

        rules = [
            {
                'id': row['id'],
                'pattern': row['pattern'],
                'account_id': row['default_account_id'],
                'account_name': row['account_name'],
                'account_number': row['account_number'],
                'confidence': row['confidence'],
                'times_used': row['times_used']
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return rules

    @staticmethod
    def delete_rule(rule_id: int):
        """Delete a categorization rule."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM categorization_rules WHERE id = ?", (rule_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def update_rule(rule_id: int, account_id: int):
        """Update the account for a categorization rule."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE categorization_rules SET default_account_id = ? WHERE id = ?",
            (account_id, rule_id)
        )
        conn.commit()
        conn.close()
