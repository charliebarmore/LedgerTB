"""Exercise the public hygiene CLI in disposable Git repositories only."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_public_hygiene.py"


class PublicHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def add(self, name, content="fictional fixture", tracked=True):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if tracked:
            subprocess.run(["git", "add", "-f", "--", name], cwd=self.root, check=True)

    def run_check(self):
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=self.root, capture_output=True, text=True)

    def test_allows_public_evidence_and_ignores_untracked_local_work(self):
        self.add("docs/results.json", '{"client": "Fictional Studio", "passed": true}')
        self.add("README.md", "https://github.com/example/project\nUse ~/projects/repo\n")
        self.add(".env.example", "PROVIDER_KEY=\n")
        self.add("output/private.ledgertb", tracked=False)
        self.assertEqual(self.run_check().returncode, 0)

    def test_rejects_force_added_private_files(self):
        names = ["output/results.txt", "tmp/log.txt", ".worktrees/copy/a.py", "internal/report.md",
                 "data/book.ledgertb", "data/book.ledgertb-wal", "data/book.db-shm",
                 "data/book.sqlite3", ".env.local", "signing.env", "certificate.p12", "private.key"]
        for name in names:
            self.add(name)
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        for name in names:
            self.assertIn(name, result.stdout)

    def test_rejects_metadata_without_echoing_values(self):
        values = ["/Users/fictional-owner/private/", r"C:\Users\fictional-owner\private",
                  "https://app.notion.com/p/fictional-page", "subagentId: fictional-session",
                  "/cursor/stores/fictional-session/", "book_key_" + "a1" * 32]
        self.add("docs/private.md", "\n".join(values))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("docs/private.md:"), len(values))
        for value in values:
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_document_symlink_is_not_followed(self):
        self.add("README.md", "safe")
        (self.root / "README.md").unlink()
        (self.root / "README.md").symlink_to(SCRIPT)
        self.assertEqual(self.run_check().returncode, 1)

    def test_fails_closed_outside_git(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(SCRIPT)], cwd=directory, capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
