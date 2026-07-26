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
