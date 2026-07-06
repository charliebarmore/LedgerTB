from typing import List, Dict, Optional
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from models.account import Account


# Forces the model to return structured, schema-valid output instead of free
# text we'd otherwise have to regex/JSON-parse out of a chat response.
_CATEGORIZE_TOOL = {
    "name": "categorize_transactions",
    "description": "Suggest an account categorization for each transaction.",
    "input_schema": {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "1-based transaction number from the prompt"
                        },
                        "account_number": {
                            "type": "string",
                            "description": "The suggested account's number"
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"]
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief explanation (1 sentence)"
                        }
                    },
                    "required": ["index", "account_number", "confidence", "reason"]
                }
            }
        },
        "required": ["suggestions"]
    }
}


class CategorizationService:
    """Uses Claude API to suggest account categorizations for transactions."""

    def __init__(self):
        self.client = None
        if ANTHROPIC_API_KEY:
            self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        # Result state from the most recent run (always present so callers never
        # read a stale value or hit a missing attribute).
        self.last_matched = 0
        self.last_total = 0
        self.last_unmatched = []
        self.last_error = None

    def is_available(self) -> bool:
        """Check if the API is configured and available."""
        return self.client is not None

    def categorize_transactions(
        self,
        transactions: List[Dict],
        accounts: List[Account],
        batch_size: int = 25
    ) -> List[Dict]:
        """
        Use Claude to suggest account categorizations for a list of transactions.

        Args:
            transactions: List of dicts with 'description' and 'amount'
            accounts: List of available Account objects
            batch_size: Number of transactions to process at once (default 25)

        Returns:
            List of dicts with added 'suggested_account_id' and 'confidence'
        """
        if not self.is_available():
            return transactions

        # Process in batches to avoid response truncation
        if len(transactions) > batch_size:
            total_matched = 0
            total_processed = 0
            all_errors = []

            for i in range(0, len(transactions), batch_size):
                batch = transactions[i:i + batch_size]
                self._categorize_batch(batch, accounts)

                if hasattr(self, 'last_matched'):
                    total_matched += self.last_matched
                if hasattr(self, 'last_total'):
                    total_processed += self.last_total
                if hasattr(self, 'last_error') and self.last_error:
                    all_errors.append(f"Batch {i//batch_size + 1}: {self.last_error}")

            # Update summary stats
            self.last_matched = total_matched
            self.last_total = total_processed
            self.last_error = "; ".join(all_errors) if all_errors else None

            return transactions

        return self._categorize_batch(transactions, accounts)

    def _categorize_batch(
        self,
        transactions: List[Dict],
        accounts: List[Account]
    ) -> List[Dict]:
        """
        Categorize a single batch of transactions.

        Args:
            transactions: List of dicts with 'description' and 'amount'
            accounts: List of available Account objects

        Returns:
            List of dicts with added 'suggested_account_id' and 'confidence'
        """
        # Reset per-batch result state up front so a failure (or an early return)
        # can never leave stale values from a previous batch.
        self.last_matched = 0
        self.last_total = 0
        self.last_unmatched = []
        self.last_error = None

        if not transactions:
            return transactions

        # Build account list for prompt
        account_list = "\n".join([
            f"- {a.account_number}: {a.name} ({a.type})"
            for a in accounts
            if a.is_active
        ])

        # Build transaction list for prompt
        transaction_text = "\n".join([
            f"{i+1}. [{t['date']}] {t['description']} | ${t['amount']:,.2f}"
            for i, t in enumerate(transactions)
        ])

        prompt = f"""You are an accounting assistant helping categorize bank transactions for a CPA firm.

Available accounts:
{account_list}

Transactions to categorize:
{transaction_text}

For each transaction, determine the most appropriate expense or revenue account.
- Negative amounts are expenses/withdrawals - match to an Expense account
- Positive amounts are deposits - match to a Revenue account
- If unsure, use "7500: Miscellaneous Expense" for expenses or "4900: Other Income" for revenue

Call the categorize_transactions tool with a suggestion for every transaction listed above."""

        try:
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4000,
                tools=[_CATEGORIZE_TOOL],
                tool_choice={"type": "tool", "name": "categorize_transactions"},
                messages=[{"role": "user", "content": prompt}]
            )

            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if tool_use is None:
                raise ValueError("Model response did not include a categorize_transactions tool call")

            suggestions = tool_use.input.get("suggestions", [])
            matched_count, unmatched_accounts = self._apply_suggestions(
                transactions, suggestions, accounts
            )

            self.last_matched = matched_count
            self.last_total = len(transactions)
            self.last_unmatched = unmatched_accounts
            self.last_error = None

        except Exception as e:
            self.last_error = str(e)
            # last_matched/last_total/last_unmatched remain at their reset values.

        return transactions

    @staticmethod
    def _apply_suggestions(transactions, suggestions, accounts):
        """Apply model suggestions to transactions in place.

        Validates the model-supplied 1-based ``index``: out-of-range indices and
        duplicate indices are ignored, so a suggestion can never be written to
        the wrong transaction or overwrite an already-suggested one (only the
        first suggestion for a given index is honored). Returns
        ``(matched_count, unmatched_account_numbers)``.
        """
        # Account lookup - handle both string and int account numbers
        account_lookup = {}
        for a in accounts:
            account_lookup[a.account_number] = a.id
            account_lookup[str(a.account_number)] = a.id

        matched_count = 0
        unmatched_accounts = []
        seen_indices = set()
        for suggestion in suggestions:
            idx = suggestion.get('index', 0) - 1
            if not (0 <= idx < len(transactions)):
                continue  # index the model invented / out of range
            if idx in seen_indices:
                continue  # duplicate index - keep only the first suggestion
            seen_indices.add(idx)

            account_num = str(suggestion.get('account_number', ''))
            if account_num in account_lookup:
                transactions[idx]['suggested_account_id'] = account_lookup[account_num]
                transactions[idx]['confidence'] = suggestion.get('confidence', 'medium')
                transactions[idx]['reason'] = suggestion.get('reason', 'AI suggested')
                matched_count += 1
            else:
                unmatched_accounts.append(account_num)

        return matched_count, unmatched_accounts

    def categorize_single(
        self,
        description: str,
        amount: float,
        accounts: List[Account]
    ) -> Optional[Dict]:
        """
        Categorize a single transaction.

        Returns:
            Dict with 'account_id', 'confidence', 'reason' or None if unavailable
        """
        if not self.is_available():
            return None

        transactions = [{'date': '', 'description': description, 'amount': amount}]
        result = self.categorize_transactions(transactions, accounts)

        if result and 'suggested_account_id' in result[0]:
            return {
                'account_id': result[0]['suggested_account_id'],
                'confidence': result[0].get('confidence', 'medium'),
                'reason': result[0].get('reason', '')
            }

        return None
