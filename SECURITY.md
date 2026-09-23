# Security Policy

## Supported versions

Security fixes target the latest published LedgerTB release and the current
`main` branch. Older releases may not receive security updates; reproduce an
issue against the latest version when practical.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability.

Email **info@ledgerlabs.co** with the subject `LedgerTB security report` and
include:

- the affected version or commit;
- the operating system and installation method;
- the security impact and affected feature;
- concise reproduction steps or a proof of concept; and
- any suggested mitigation, if known.

Do not send real client book files, credentials, API keys, tax identifiers, or
other personal or financial data. Use sanitized sample data and redact logs and
screenshots before attaching them.

Ledger Labs will acknowledge reports as promptly as practical, investigate in
good faith, and coordinate a reasonable disclosure timeline with the reporter.
Please allow time for a fix and affected-user guidance before publishing
technical details.

For ordinary bugs and feature requests that do not expose data or cross a
security boundary, use the public GitHub issue tracker instead.

## Secret scanning

CI scans every tracked file with detect-secrets 1.5.0. `.secrets.baseline`
contains individually reviewed false positives: content/commit hashes in
synthetic evaluation evidence and explicit dummy credentials in test fixtures.
Historical entries may remain after the corresponding documentation is removed;
they do not authorize restoring private metadata. Entries match a value fingerprint and filename;
they do not exempt directories or suppress new values. No real credential is
approved for the baseline. Review each new finding before adding an exception;
do not blindly regenerate it to make CI pass.

The local gitleaks hook retains its default rules. Its additional exception
requires both the exact `.secrets.baseline` path and a `hashed_secret` field
containing a 40-character SHA-1 fingerprint; other fields and files remain
scanned. This prevents the scanner's own fingerprints being mistaken for keys.


## Public repository hygiene

CI also runs `python scripts/check_public_hygiene.py` against tracked files.
It rejects common local output directories, book/database files, environment
files and signing keys, plus recognizable private workspace, agent-session and
vault identifiers in documentation. Sanitized synthetic evaluation results and
public release hashes remain useful evidence and belong in the repository.

These checks complement secret scanning and human diff review; they cannot
identify every private value or inspect previous commits, PR conversations,
release assets or CI artifacts. Removing information in a new commit does not
remove it from Git history. Suspected credentials require separate incident
review and rotation; coordinate any history rewrite with maintainers.
