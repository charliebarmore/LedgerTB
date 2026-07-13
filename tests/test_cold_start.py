import os
import subprocess
import sys


def test_database_connection_imports_in_clean_process(tmp_path):
    """Mirror the installed app's cold import order in an isolated interpreter."""
    env = dict(os.environ, PROBOOKS_DB_PATH=str(tmp_path / "cold-start.db"))
    result = subprocess.run(
        [sys.executable, "-c", "from database import init_database; init_database()"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
