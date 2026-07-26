"""Sign convention should follow the account, and follow it when it changes."""
import pytest

from services.csv_import import SIGN_CONVENTIONS, default_sign_convention


def test_liability_accounts_default_to_the_credit_card_convention():
    """Credit cards print purchases positive; picking one should be enough."""
    assert default_sign_convention("Liability") == "credit_card"


def test_asset_accounts_default_to_the_bank_convention():
    assert default_sign_convention("Asset") == "bank"


@pytest.mark.parametrize("account_type", [None, "", "Equity", "Revenue", "Expense"])
def test_anything_else_falls_back_to_bank(account_type):
    """Bank is the safer fallback: it leaves amounts as the file states them."""
    assert default_sign_convention(account_type) == "bank"


def test_every_default_is_a_real_option():
    for account_type in ("Asset", "Liability", None):
        assert default_sign_convention(account_type) in SIGN_CONVENTIONS


class FakeSessionState(dict):
    """Stands in for st.session_state, which needs a running script."""


@pytest.fixture
def session(monkeypatch):
    from utils import ui

    state = FakeSessionState()
    monkeypatch.setattr(ui.st, "session_state", state)
    return state


def test_default_is_applied_on_first_render(session):
    from utils.ui import apply_default_on_change

    apply_default_on_change("conv", depends_on=7, default_value="credit_card")
    assert session["conv"] == "credit_card"


def test_default_is_reapplied_when_the_account_changes(session):
    """Switching from a bank account to a credit card must move the convention."""
    from utils.ui import apply_default_on_change

    apply_default_on_change("conv", depends_on=1, default_value="bank")
    assert session["conv"] == "bank"

    apply_default_on_change("conv", depends_on=2, default_value="credit_card")
    assert session["conv"] == "credit_card"


def test_a_user_override_survives_reruns_on_the_same_account(session):
    """The default must not fight the user on every unrelated rerun."""
    from utils.ui import apply_default_on_change

    apply_default_on_change("conv", depends_on=1, default_value="bank")
    session["conv"] = "flip"          # the user picks something else

    for _ in range(3):                # unrelated reruns
        apply_default_on_change("conv", depends_on=1, default_value="bank")

    assert session["conv"] == "flip"


def test_an_override_is_replaced_once_the_account_changes(session):
    """An override belongs to the account it was made for."""
    from utils.ui import apply_default_on_change

    apply_default_on_change("conv", depends_on=1, default_value="bank")
    session["conv"] = "flip"

    apply_default_on_change("conv", depends_on=2, default_value="credit_card")
    assert session["conv"] == "credit_card"


def test_a_none_dependency_is_tracked_not_treated_as_unseen(session):
    """No account selected is a real state; it must not re-fire every run."""
    from utils.ui import apply_default_on_change

    apply_default_on_change("conv", depends_on=None, default_value="bank")
    session["conv"] = "flip"
    apply_default_on_change("conv", depends_on=None, default_value="bank")

    assert session["conv"] == "flip"


def test_separate_widgets_track_their_dependencies_independently(session):
    """The CSV tab and the statement tab must not clobber each other."""
    from utils.ui import apply_default_on_change

    apply_default_on_change("csv_conv", depends_on=1, default_value="bank")
    apply_default_on_change("doc_conv", depends_on=2, default_value="credit_card")

    assert session["csv_conv"] == "bank"
    assert session["doc_conv"] == "credit_card"

    apply_default_on_change("csv_conv", depends_on=9, default_value="credit_card")
    assert session["csv_conv"] == "credit_card"
    assert session["doc_conv"] == "credit_card"
