import pytest

from services.csv_import import CSVImporter


def test_csv_parser_preserves_durable_source_row_identity():
    rows = CSVImporter.parse_csv(
        "Date,Description,Amount\n01/02/2026,Coffee,-4.50\n01/03/2026,Deposit,100.00\n",
        date_column="Date",
        description_column="Description",
        amount_column="Amount",
        source_id="content-sha256",
        source_filename="january.csv",
    )

    assert [row["source_row_number"] for row in rows] == [2, 3]
    assert {row["source_id"] for row in rows} == {"content-sha256"}
    assert {row["source_filename"] for row in rows} == {"january.csv"}


def _csv(row_count):
    header = "Date,Description,Amount\n"
    rows = "".join(
        f"2026-01-{(n % 28) + 1:02d},MERCHANT {n},-{n}.00\n" for n in range(1, row_count + 1)
    )
    return header + rows


def test_decode_upload_supports_common_bank_export_encodings():
    assert CSVImporter.decode_upload(b"\xef\xbb\xbfDate,Description\n") == (
        "Date,Description\n"
    )
    assert "Café" in CSVImporter.decode_upload(
        "Date,Description\n1/2/2026,Café\n".encode("cp1252")
    )
    assert "Description" in CSVImporter.decode_upload(
        "Date,Description\n".encode("utf-16")
    )


def test_preview_samples_ten_rows_by_default():
    df, columns = CSVImporter.preview_csv(_csv(45))

    assert len(df) == 10
    assert columns == ["Date", "Description", "Amount"]


def test_preview_returns_every_row_when_num_rows_is_none():
    """The import page shows the whole file; a truncated table reads as a short file."""
    df, _ = CSVImporter.preview_csv(_csv(45), num_rows=None)

    assert len(df) == 45
    assert df.iloc[-1]["Description"] == "MERCHANT 45"


def test_preview_of_a_file_shorter_than_the_sample_size():
    df, _ = CSVImporter.preview_csv(_csv(3), num_rows=None)
    assert len(df) == 3


# --- directional import totals -------------------------------------------

from services.csv_import import apply_sign_convention, summarize_import_amounts

# A credit-card statement as printed: purchases positive, payments negative.
CARD_ROWS = [79.00, 15.00, -100.00, 9.99, -1.81]
# A bank statement as printed: deposits positive, withdrawals negative.
BANK_ROWS = [450.00, -26.18, 1925.00, -18.75]


def test_bank_amounts_pass_through_unchanged():
    assert apply_sign_convention(-26.18, "bank") == -26.18
    assert apply_sign_convention(450.00, "bank") == 450.00


def test_credit_card_and_flip_negate():
    assert apply_sign_convention(79.00, "credit_card") == -79.00
    assert apply_sign_convention(-100.00, "credit_card") == 100.00
    assert apply_sign_convention(79.00, "flip") == -79.00


def test_card_totals_read_as_charges_and_payments():
    summary = summarize_import_amounts(CARD_ROWS, "credit_card", "Liability")

    assert summary["outflow_label"] == "Total charges"
    assert summary["inflow_label"] == "Total payments"
    assert summary["outflow"] == 103.99      # 79.00 + 15.00 + 9.99
    assert summary["inflow"] == 101.81       # 100.00 + 1.81
    assert summary["net"] == -2.18


def test_bank_totals_read_as_receipts_and_disbursements():
    summary = summarize_import_amounts(BANK_ROWS, "bank", "Asset")

    assert summary["outflow_label"] == "Total disbursements"
    assert summary["inflow_label"] == "Total receipts"
    assert summary["outflow"] == 44.93       # 26.18 + 18.75
    assert summary["inflow"] == 2375.00      # 450.00 + 1925.00
    assert summary["net"] == 2330.07


def test_identical_charges_report_a_meaningful_total():
    """The case that prompted this: a range read "79.00 to 79.00"."""
    summary = summarize_import_amounts([79.00, 79.00], "credit_card", "Liability")

    assert summary["outflow"] == 158.00
    assert summary["inflow"] == 0.0
    assert summary["net"] == -158.00


def test_totals_follow_the_sign_convention_not_the_account():
    """Choosing Flip on a card statement must move the amounts between buckets."""
    as_card = summarize_import_amounts(CARD_ROWS, "credit_card", "Liability")
    as_bank = summarize_import_amounts(CARD_ROWS, "bank", "Liability")

    assert as_card["outflow"] == as_bank["inflow"]
    assert as_card["inflow"] == as_bank["outflow"]
    assert as_card["net"] == -as_bank["net"]


def test_unknown_account_type_uses_bank_wording():
    summary = summarize_import_amounts(BANK_ROWS, "bank", None)
    assert summary["outflow_label"] == "Total disbursements"


def test_summary_of_an_empty_file_is_zero_not_an_error():
    summary = summarize_import_amounts([], "bank", "Asset")
    assert summary["outflow"] == 0 and summary["inflow"] == 0 and summary["net"] == 0


def test_net_equals_the_sum_of_normalized_amounts():
    """Net must reconcile to the account's actual movement."""
    summary = summarize_import_amounts(CARD_ROWS, "credit_card", "Liability")
    expected = round(sum(apply_sign_convention(a, "credit_card") for a in CARD_ROWS), 2)
    assert summary["net"] == expected


def test_european_amounts_no_longer_post_at_a_hundred_times_their_value():
    """The worst find in the pre-launch audit: "1,23" (one euro twenty-three)
    posted as $123.00. Both legs took the same wrong figure, so the entry
    balanced, the trial balance tied, and nothing anywhere flagged it."""
    from services.csv_import import parse_amount

    assert parse_amount("1,23") == 1.23
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("0,05") == 0.05
    # US formatting must keep working exactly as before.
    assert parse_amount("1,234") == 1234.0
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("1,234,567.89") == 1234567.89
    # Signs, currency marks and padding survive the rewrite.
    assert parse_amount("(100.00)") == -100.0
    assert parse_amount("100.00-") == -100.0
    assert parse_amount("$1,234.56") == 1234.56
    assert parse_amount("  12.50 ") == 12.5


def test_absurd_csv_shapes_are_refused_before_pandas_sees_them():
    """A one-line file of a million columns used to wedge the whole desktop
    app for minutes — and the parse runs on file selection, before any
    confirmation. The check must beat the parse, not follow it."""
    import time

    from services.csv_import import CSVImporter, CsvTooLarge

    wide = ",".join(f"c{i}" for i in range(200_000)) + "\n"
    started = time.monotonic()
    with pytest.raises(CsvTooLarge):
        CSVImporter.preview_csv(wide)
    assert time.monotonic() - started < 5, "the guard ran after the parse"

    with pytest.raises(CsvTooLarge):
        CSVImporter.decode_upload(b"x" * (26 * 1024 * 1024))


def test_undecodable_statement_bytes_do_not_raise():
    """A stray cp1252 byte used to surface a raw UnicodeDecodeError."""
    from services.csv_import import CSVImporter

    text = CSVImporter.decode_upload(b"Date,Description,Amount\n01/01/2026,AC\x81ME,-1.00\n")
    assert "AC" in text
    # A truncated UTF-16 BOM file must fall back rather than explode.
    assert CSVImporter.decode_upload(b"\xff\xfeAB\x41") is not None
