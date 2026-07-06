import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base directory
BASE_DIR = Path(__file__).parent

# Database.
# Defaults to a repo-local file for development/test data (gitignored). The
# location is overridable via the PROBOOKS_DB_PATH env var.
#
# IMPORTANT (FTC Safeguards): before this app holds REAL client financial data,
# point PROBOOKS_DB_PATH at a file under ~/Practice so the database is covered by
# the sanctioned Backblaze encrypted backup with the customer-managed key. The
# default repo-local path under ~/LedgerLabs is for test/seed data only and is
# NOT a sanctioned location for client PII.
DATABASE_PATH = Path(os.getenv("PROBOOKS_DB_PATH", BASE_DIR / "data" / "accounting.db"))

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# Application settings
APP_NAME = "ProBooks"
FISCAL_YEAR_START_MONTH = 1  # January
