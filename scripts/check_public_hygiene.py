"""Reject common private artifacts in tracked files; complements secret scanning.

This is a targeted guard, not a privacy classifier or a Git-history scrubber.
Only documentation is checked for workspace metadata so synthetic source-code
fixtures can continue to exercise path and credential handling.
"""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


PRIVATE_ROOTS = {"output", "tmp", ".worktrees", "internal"}
PRIVATE_FILE = re.compile(
    r"(?:\.(?:db|sqlite|sqlite3|ledgertb)(?:[.-].*)?"
    r"|\.(?:p12|pfx|pem|key))$", re.IGNORECASE
)
DOCUMENT_TYPES = {".md", ".rst", ".txt", ".json", ".html"}
PRIVATE_TEXT = {
    "personal home directory": re.compile(r"/(?:Users|home)/[\w.-]+/|[A-Za-z]:\\Users\\[\w.-]+\\"),
    "private workspace link": re.compile(r"https?://(?:app\.)?notion\.(?:com|so)/"),
    "agent session metadata": re.compile(r"/cursor/stores/|cursor\.com/agents/|subagentId\s*:"),
    "concrete vault identifier": re.compile(r"book_key_[0-9a-f]{16,}|mcp_book:[0-9a-f]{16,}", re.IGNORECASE),
}


def check(root: Path) -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    findings = []
    for name in filter(None, tracked):
        path = PurePosixPath(name)
        env_file = (path.name == ".env" or path.name.startswith(".env.") or path.name.endswith(".env"))
        env_template = path.name.endswith((".env.example", ".env.sample"))
        if path.parts[0] in PRIVATE_ROOTS or PRIVATE_FILE.search(name) or (env_file and not env_template):
            findings.append(f"{name}: private/local artifact")
            continue
        if path.suffix.lower() not in DOCUMENT_TYPES:
            continue
        file = root / name
        # Do not follow links into local files outside the checkout.
        if file.is_symlink():
            findings.append(f"{name}: documentation symlink requires review")
            continue
        if not file.exists():  # A tracked deletion waiting to be staged.
            continue
        content = file.read_text(encoding="utf-8")
        for number, line in enumerate(content.splitlines(), 1):
            for category, pattern in PRIVATE_TEXT.items():
                if pattern.search(line):
                    findings.append(f"{name}:{number}: {category}")
    return findings


def main() -> int:
    try:
        root = Path(subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True,
            capture_output=True, text=True,
        ).stdout.strip())
        findings = check(root)
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        print("Public hygiene check could not read the tracked repository.", file=sys.stderr)
        return 2
    if findings:
        print("Public hygiene check failed (matched values are omitted):")
        print("\n".join(findings))
        return 1
    print("Public hygiene check passed for tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
