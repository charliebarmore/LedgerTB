-- Per-client identity for deliverables plus an append-only assistant proposal
-- inbox. Logos stay human-controlled; the assistant may propose text/color
-- values, which do not take effect until a person accepts them in the app.
CREATE TABLE client_branding (
    client_id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    tagline TEXT NOT NULL DEFAULT '',
    accent_hex TEXT NOT NULL DEFAULT '',
    logo BLOB,
    logo_mime TEXT,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE client_branding_proposals (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    display_name TEXT,
    tagline TEXT,
    accent_hex TEXT,
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'accepted', 'dismissed')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_by TEXT,
    resolved_at TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE INDEX idx_client_branding_proposals_pending
    ON client_branding_proposals(client_id, status, id);
