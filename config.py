import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import platformdirs
from version import APP_VERSION

load_dotenv()

# Base directory
BASE_DIR = Path(__file__).parent

# A PyInstaller bundle is read-only, so a frozen build must keep its writable
# data (database, saved API key) in a per-user app-data directory. Running from
# source keeps everything in the repo -- unchanged dev/test behavior.
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    USER_DATA_DIR = Path(platformdirs.user_data_dir("ProBooks", "LedgerLabs"))
else:
    USER_DATA_DIR = BASE_DIR / "data"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database.
# Defaults to USER_DATA_DIR (repo-local `data/` in source; app-data dir when
# frozen). Overridable via the PROBOOKS_DB_PATH env var.
#
# IMPORTANT (FTC Safeguards): before this app holds REAL client financial data,
# point PROBOOKS_DB_PATH at a file under ~/Practice so the database is covered by
# the sanctioned Backblaze encrypted backup with the customer-managed key. The
# default location is for test/seed data only and is NOT a sanctioned location
# for client PII.
DATABASE_PATH = Path(os.getenv("PROBOOKS_DB_PATH", USER_DATA_DIR / "accounting.db"))
BACKUP_DIR = Path(os.getenv("PROBOOKS_BACKUP_DIR", USER_DATA_DIR / "backups"))

# Anthropic API key.
# Priority: environment/.env (dev), then a key file saved by the in-app setup
# form. The key file lives in USER_DATA_DIR so it stays writable in a read-only
# bundle. (data/ is gitignored, so the key is never committed.)
API_KEY_FILE = USER_DATA_DIR / "anthropic_api_key"


def _read_saved_api_key() -> str:
    from utils.secure_store import get_secret, migrate_legacy_secret
    return (
        get_secret("anthropic_api_key")
        or migrate_legacy_secret("anthropic_api_key", API_KEY_FILE)
        or ""
    )


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "") or _read_saved_api_key()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# Application settings
APP_NAME = "ProBooks"
FISCAL_YEAR_START_MONTH = 1  # January
