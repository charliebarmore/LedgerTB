import os
import subprocess
import sys


def test_database_connection_imports_in_clean_process(tmp_path):
    """Mirror the installed app's cold import order in an isolated interpreter.

    init_database now requires the passphrase-derived key to be set first (the
    unlock gate does this in the app), so the probe sets a key before init.
    """
    env = dict(os.environ, PROBOOKS_DB_PATH=str(tmp_path / "cold-start.db"))
    code = (
        "from database import connection, init_database\n"
        "from database.crypto import derive_key\n"
        "connection.set_active_key(derive_key('cold-start-pass'))\n"
        "init_database()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
