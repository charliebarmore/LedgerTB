"""Assistant prose keeps currency without turning it into a math expression."""
import pytest

from utils.ui import escape_currency


@pytest.mark.parametrize("source, expected", [
    ("Accrue $1,200 against $1,200 payable.", r"Accrue \$1,200 against \$1,200 payable."),
    (r"Already \$25 and $30", r"Already \$25 and \$30"),
    ("One $25 amount\non two lines", "One \\$25 amount\non two lines"),
    (r"Literal slash \\$25", r"Literal slash \\\$25"),
    ("**Support** and [link](https://example.com)", "**Support** and [link](https://example.com)"),
    ("", ""),
])
def test_currency_is_literal_and_escaping_is_idempotent(source, expected):
    assert escape_currency(source) == expected
    assert escape_currency(expected) == expected
