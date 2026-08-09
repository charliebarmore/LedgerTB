# Contributing to ProBooks

Thanks for helping improve ProBooks. Contributions that make the application
safer, clearer, and more dependable for accounting professionals are welcome.

## Before opening an issue

- Search existing issues before creating a duplicate.
- Use sanitized sample data. Never attach a real client book, bank statement,
  API key, credential, tax identifier, or other personal or financial data.
- Report suspected vulnerabilities privately as described in `SECURITY.md`.

## Development setup

ProBooks is tested with Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
streamlit run app.py --server.address=127.0.0.1
```

Some tests are platform-specific and may be skipped outside their target
operating system. Desktop packaging instructions are in `DESKTOP.md`.

## Making a change

1. Create a focused branch from the latest `main`.
2. Keep the change small enough to review and explain why it is needed.
3. Add or update tests for behavior changes and bug fixes.
4. Run `python -m pytest -q` and document any skipped or unverified checks.
5. Update user-facing documentation when behavior or setup changes.
6. Open a pull request using the repository template.

Changes to posting, imports, reports, migrations, encryption, backups, or
assistant permissions need particular care. Preserve double-entry invariants,
client isolation, audit history, review checkpoints, and secure defaults. Never
silently discard or reinterpret financial data.

Dependency changes should be intentional and narrowly scoped. Update the
applicable lock file when a release dependency changes, then test installation
and packaging on the target platform.

## Pull requests

Maintainers may ask for changes, additional tests, or a smaller scope. A pull
request should state what changed, why, how it was verified, and any remaining
risks. By contributing, you agree that your contribution is licensed under the
MIT License in `LICENSE`.
