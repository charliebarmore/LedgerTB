"""Every supported launch path must keep the unlocked book on loopback."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_streamlit_config_binds_to_loopback():
    with (ROOT / ".streamlit" / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    assert config["server"]["address"] == "127.0.0.1"


def test_launchers_and_source_instructions_bind_to_loopback():
    for relative_path in ("desktop.py", "run_ledgertb.py", "README.md"):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "server.address=127.0.0.1" in contents, relative_path
