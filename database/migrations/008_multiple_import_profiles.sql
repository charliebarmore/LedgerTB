-- Allow multiple named CSV export formats per client account while preserving
-- the single profile introduced in migration 007 as a legacy "Default" format.
ALTER TABLE import_profiles RENAME TO import_profiles_single;

CREATE TABLE import_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    date_column TEXT NOT NULL,
    description_column TEXT NOT NULL,
    amount_format TEXT NOT NULL CHECK(amount_format IN ('single', 'separate')),
    amount_column TEXT,
    debit_column TEXT,
    credit_column TEXT,
    sign_convention TEXT NOT NULL CHECK(sign_convention IN ('bank', 'credit_card', 'flip')),
    header_signature TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (bank_account_id) REFERENCES accounts(id),
    UNIQUE(client_id, bank_account_id, name),
    CHECK(
        (amount_format = 'single' AND amount_column IS NOT NULL
         AND debit_column IS NULL AND credit_column IS NULL)
        OR
        (amount_format = 'separate' AND amount_column IS NULL
         AND (debit_column IS NOT NULL OR credit_column IS NOT NULL))
    )
);

INSERT INTO import_profiles
    (id, client_id, bank_account_id, name, date_column, description_column,
     amount_format, amount_column, debit_column, credit_column, sign_convention,
     header_signature, created_at, updated_at)
SELECT id, client_id, bank_account_id, 'Default', date_column, description_column,
       amount_format, amount_column, debit_column, credit_column, sign_convention,
       NULL, created_at, updated_at
FROM import_profiles_single;

DROP TABLE import_profiles_single;

CREATE INDEX idx_import_profiles_client_account
    ON import_profiles(client_id, bank_account_id);
