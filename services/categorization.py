from typing import List, Dict, Optional
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY
from models.account import Account


class CategorizationService:
    """Uses Claude API to suggest account categorizations for transactions."""

    def __init__(self):
        self.client = None
        if ANTHROPIC_API_KEY:
            self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def is_available(self) -> bool:
        """Check if the API is configured and available."""
        return self.client is not None

    def categorize_transactions(
        self,
        transactions: List[Dict],
        accounts: List[Account]
    ) -> List[Dict]:
        """
        Use Claude to suggest account categorizations for a list of transactions.

        Args:
            transactions: List of dicts with 'description' and 'amount'
            accounts: List of available Account objects

        Returns:
            List of dicts with added 'suggested_account_id' and 'confidence'
        """
        if not self.is_available():
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

Respond with a JSON array where each element has:
- "index": the transaction number (1-based)
- "account_number": the suggested account number
- "confidence": your confidence level (high, medium, low)
- "reason": brief explanation (1 sentence)

Example response format:
[
  {{"index": 1, "account_number": "6300", "confidence": "high", "reason": "Software subscription payment"}},
  {{"index": 2, "account_number": "4000", "confidence": "high", "reason": "Client payment for services"}}
]

Respond only with the JSON array, no other text."""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse response
            response_text = response.content[0].text.strip()

            # Clean up response if needed
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            import json
            suggestions = json.loads(response_text)

            # Build account lookup
            account_lookup = {a.account_number: a.id for a in accounts}

            # Apply suggestions to transactions
            for suggestion in suggestions:
                idx = suggestion['index'] - 1
                if 0 <= idx < len(transactions):
                    account_num = suggestion['account_number']
                    if account_num in account_lookup:
                        transactions[idx]['suggested_account_id'] = account_lookup[account_num]
                        transactions[idx]['confidence'] = suggestion.get('confidence', 'medium')
                        transactions[idx]['reason'] = suggestion.get('reason', '')

        except Exception as e:
            print(f"Categorization error: {e}")
            # Return transactions unchanged if API fails

        return transactions

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
