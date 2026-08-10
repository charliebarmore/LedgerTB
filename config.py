import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import platformdirs
from version import APP_VERSION

load_dotenv()

# Base directory
BASE_DIR = Path(__file__).parent


def app_env(suffix: str, default=None):
    """Read the LedgerTB setting, falling back to its ProBooks-era name.

    The old prefix remains supported so existing firm deployments and MCP
    configurations keep working after the product rename.
    """
    return (
        os.getenv(f"LEDGERTB_{suffix}")
        or os.getenv(f"PROBOOKS_{suffix}")
        or default
    )


def _contains_existing_data(path: Path) -> bool:
    """Whether a product-data directory contains an existing installation."""
    return any((path / name).exists() for name in (
        "accounting.db", "books.json", "Books", "backups", "anthropic_api_key",
    ))


def choose_user_data_dir(current: Path, legacy: Path) -> Path:
    """Use the newest populated data home, else a populated ProBooks home.

    This deliberately does not move financial data during startup. Existing
    users continue in place, while new installations get the new branded path.
    """
    if _contains_existing_data(current):
        return current
    return legacy if _contains_existing_data(legacy) else current

# A PyInstaller bundle is read-only, so a frozen build must keep its writable
# data (database, saved API key) in a per-user app-data directory. Running from
# source keeps everything in the repo -- unchanged dev/test behavior.
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    LEDGERTB_USER_DATA_DIR = Path(
        platformdirs.user_data_dir("LedgerTB", "LedgerLabs")
    )
    LEGACY_USER_DATA_DIR = Path(
        platformdirs.user_data_dir("ProBooks", "LedgerLabs")
    )
    USER_DATA_DIR = choose_user_data_dir(
        LEDGERTB_USER_DATA_DIR, LEGACY_USER_DATA_DIR
    )
else:
    USER_DATA_DIR = BASE_DIR / "data"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database.
# Defaults to USER_DATA_DIR (repo-local `data/` in source; app-data dir when
# frozen). Overridable via the LEDGERTB_DB_PATH env var.
#
# IMPORTANT (FTC Safeguards): before this app holds REAL client financial data,
# point LEDGERTB_DB_PATH at a location your firm's encrypted backup covers.
# The default location is for test/seed data only and is not part of any
# backup plan you haven't set up yourself.
DATABASE_PATH = Path(app_env("DB_PATH", USER_DATA_DIR / "accounting.db"))
BACKUP_DIR = Path(app_env("BACKUP_DIR", USER_DATA_DIR / "backups"))

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
APP_NAME = "LedgerTB"
FISCAL_YEAR_START_MONTH = 1  # January
