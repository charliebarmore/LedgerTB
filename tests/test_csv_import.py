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
