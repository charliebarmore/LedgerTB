-- Account-level close evidence and signoff. Reusable account mappings are
-- separate from fiscal-period review work so groupings carry forward while
-- explanations, notes, evidence, and signatures never do.
CREATE TABLE lead_sheet_groups (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    UNIQUE(client_id, code)
);

CREATE TABLE account_close_mappings (
    account_id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    lead_sheet_group_id INTEGER,
    review_requirement TEXT NOT NULL DEFAULT 'required'
        CHECK(review_requirement IN ('required', 'not_required')),
    exclusion_reason TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (lead_sheet_group_id) REFERENCES lead_sheet_groups(id)
);

CREATE TABLE account_close_reviews (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    fiscal_period_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    updated_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (fiscal_period_id) REFERENCES fiscal_periods(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE(fiscal_period_id, account_id)
);

CREATE TABLE account_close_evidence (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL CHECK(evidence_type IN (
        'workpaper', 'ledgerpdf', 'external', 'reconciliation'
    )),
    reference TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (review_id) REFERENCES account_close_reviews(id) ON DELETE CASCADE
);

CREATE TABLE account_review_notes (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_by TEXT,
    resolved_at TIMESTAMP,
    resolution TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (review_id) REFERENCES account_close_reviews(id) ON DELETE CASCADE
);

CREATE TABLE account_close_signoffs (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    review_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('preparer', 'reviewer')),
    content_fingerprint TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    signed_by TEXT NOT NULL,
    signed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (review_id) REFERENCES account_close_reviews(id) ON DELETE CASCADE
);

-- Assistant proposals are append-only. A human app process may accept or
-- dismiss one; assistant connections are never authorized to update it.
CREATE TABLE close_review_proposals (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    fiscal_period_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    explanation TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'accepted', 'dismissed')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_by TEXT,
    resolved_at TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (fiscal_period_id) REFERENCES fiscal_periods(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE INDEX idx_close_reviews_period
    ON account_close_reviews(client_id, fiscal_period_id);
CREATE INDEX idx_close_evidence_review ON account_close_evidence(review_id);
CREATE INDEX idx_close_notes_review ON account_review_notes(review_id, status);
CREATE INDEX idx_close_signoffs_review
    ON account_close_signoffs(review_id, signed_at, id);
CREATE INDEX idx_close_proposals_period
    ON close_review_proposals(client_id, fiscal_period_id, status);
