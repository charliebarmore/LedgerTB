-- Remember one CSV column/sign profile per client bank or credit-card account.
CREATE TABLE import_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL,
    date_column TEXT NOT NULL,
    description_column TEXT NOT NULL,
    amount_format TEXT NOT NULL CHECK(amount_format IN ('single', 'separate')),
    amount_column TEXT,
    debit_column TEXT,
    credit_column TEXT,
    sign_convention TEXT NOT NULL CHECK(sign_convention IN ('bank', 'credit_card', 'flip')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (bank_account_id) REFERENCES accounts(id),
    UNIQUE(client_id, bank_account_id),
    CHECK(
        (amount_format = 'single' AND amount_column IS NOT NULL
         AND debit_column IS NULL AND credit_column IS NULL)
        OR
        (amount_format = 'separate' AND amount_column IS NULL
         AND (debit_column IS NOT NULL OR credit_column IS NOT NULL))
    )
);

CREATE INDEX idx_import_profiles_client ON import_profiles(client_id);
