-- Per-client accounting policy notes the AI book reviewer must honor
-- ("ADP fees go to 7080, never Dues; GoDaddy is software"). The policy
-- travels with the client, not with whoever runs the review.
CREATE TABLE review_policies (
    client_id INTEGER PRIMARY KEY,
    policy TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);
