import json
import re
from typing import List, Dict, Optional
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
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
                self._categorize_batch(batch, accounts, start_index=i)

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

        return self._categorize_batch(transactions, accounts, start_index=0)

    def _categorize_batch(
        self,
        transactions: List[Dict],
        accounts: List[Account],
        start_index: int = 0
    ) -> List[Dict]:
        """
        Categorize a single batch of transactions.

        Args:
            transactions: List of dicts with 'description' and 'amount'
            accounts: List of available Account objects
            start_index: Starting index for error messages

        Returns:
            List of dicts with added 'suggested_account_id' and 'confidence'
        """
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
                model=ANTHROPIC_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse response
            response_text = response.content[0].text.strip()
            self.last_raw_response = response_text  # Store for debugging

            # Clean the response text - remove BOM and other invisible characters
            # Remove UTF-8 BOM if present
            if response_text.startswith('\ufeff'):
                response_text = response_text[1:]
            # Remove any other common invisible characters
            response_text = response_text.strip('\x00\x0b\x0c\r\n\t ')

            # Try to extract JSON from the response using multiple methods
            suggestions = None
            last_json_error = None

            # Method 1: Try parsing as-is first
            try:
                suggestions = json.loads(response_text)
            except json.JSONDecodeError as e:
                last_json_error = e

            # Method 2: Extract from markdown code blocks
            if suggestions is None:
                code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_text)
                if code_block_match:
                    json_text = code_block_match.group(1).strip()
                    try:
                        suggestions = json.loads(json_text)
                    except json.JSONDecodeError as e:
                        last_json_error = e

            # Method 3: Find JSON array pattern in the text
            if suggestions is None:
                # Look for array pattern starting with [ and ending with ]
                array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', response_text)
                if array_match:
                    try:
                        suggestions = json.loads(array_match.group(0))
                    except json.JSONDecodeError as e:
                        last_json_error = e

            # Method 4: Try to find the start of a JSON array
            if suggestions is None:
                start_idx = response_text.find('[')
                end_idx = response_text.rfind(']')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_text = response_text[start_idx:end_idx + 1]
                    try:
                        suggestions = json.loads(json_text)
                    except json.JSONDecodeError as e:
                        last_json_error = e
                        # Try removing trailing commas (common JSON error)
                        json_text_cleaned = re.sub(r',\s*([}\]])', r'\1', json_text)
                        try:
                            suggestions = json.loads(json_text_cleaned)
                        except json.JSONDecodeError as e:
                            last_json_error = e

            # Method 5: If response appears truncated (no closing ]), try to fix it
            if suggestions is None and response_text.count('[') > response_text.count(']'):
                # Response is likely truncated - try to close the array
                start_idx = response_text.find('[')
                if start_idx != -1:
                    # Find the last complete object (ends with })
                    last_brace = response_text.rfind('}')
                    if last_brace > start_idx:
                        json_text = response_text[start_idx:last_brace + 1] + ']'
                        try:
                            suggestions = json.loads(json_text)
                        except json.JSONDecodeError as e:
                            last_json_error = e

            if suggestions is None:
                error_detail = ""
                if last_json_error:
                    error_detail = f" Last parse error: {last_json_error}"
                raise ValueError(f"Could not parse JSON from response.{error_detail} Response preview: {response_text[:500]}...")
            self.last_suggestions = suggestions  # Store for debugging

            # Build account lookup - handle both string and int account numbers
            account_lookup = {}
            for a in accounts:
                account_lookup[a.account_number] = a.id
                account_lookup[str(a.account_number)] = a.id
            self.last_account_lookup = account_lookup  # Store for debugging

            # Apply suggestions to transactions
            matched_count = 0
            unmatched_accounts = []
            for suggestion in suggestions:
                idx = suggestion.get('index', 0) - 1
                if 0 <= idx < len(transactions):
                    account_num = str(suggestion.get('account_number', ''))
                    if account_num in account_lookup:
                        transactions[idx]['suggested_account_id'] = account_lookup[account_num]
                        transactions[idx]['confidence'] = suggestion.get('confidence', 'medium')
                        transactions[idx]['reason'] = suggestion.get('reason', 'AI suggested')
                        matched_count += 1
                    else:
                        unmatched_accounts.append(account_num)

            # Store debug info
            self.last_matched = matched_count
            self.last_total = len(suggestions)
            self.last_unmatched = unmatched_accounts
            self.last_error = None

        except json.JSONDecodeError as e:
            # Provide more helpful error message for JSON parsing issues
            raw = getattr(self, 'last_raw_response', '')
            error_context = ""
            if raw:
                # Show context around the error position
                pos = e.pos if hasattr(e, 'pos') else 0
                start = max(0, pos - 50)
                end = min(len(raw), pos + 50)
                error_context = f" Context: ...{raw[start:end]}..."

            self.last_error = f"JSON parse error: {e.msg} at position {getattr(e, 'pos', 'unknown')}.{error_context}"
            self.last_matched = 0
            self.last_total = 0
            import traceback
            self.last_traceback = traceback.format_exc()

        except Exception as e:
            self.last_error = str(e)
            self.last_matched = 0
            self.last_total = 0
            self.last_raw_response = getattr(self, 'last_raw_response', None)
            import traceback
            self.last_traceback = traceback.format_exc()

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
