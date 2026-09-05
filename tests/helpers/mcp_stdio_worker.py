"""Real MCP server/transport with only credential storage replaced for tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import books, secure_store
from utils.assistant_access import credential_names
import mcp_server


if __name__ == "__main__":
    fixture = Path(sys.argv[1])
    config = json.loads(fixture.read_text())
    book = Path(config["book"])
    books.USER_DATA_DIR = book.parent
    names = credential_names(book)

    def get_secret(name):
        # Re-read the fixture to test revocation in a long-lived server.
        current = json.loads(fixture.read_text())
        return {
            names.key: current.get("key"), names.book_id: current.get("book_id"),
            names.level: current.get("level"), names.export_roots: current.get("export_roots"),
        }.get(name)

    secure_store.get_secret = get_secret
    secure_store.set_secret = lambda *a: (_ for _ in ()).throw(AssertionError("Unexpected vault write"))
    secure_store.delete_secret = lambda *a: (_ for _ in ()).throw(AssertionError("Unexpected vault deletion"))
    raise SystemExit(mcp_server.main())
