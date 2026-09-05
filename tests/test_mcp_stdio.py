"""Discover/call tools over real stdio; retain authorization and DB enforcement."""

import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from database import connection as dbc
from models.audit_log import AuditLog
from models.draft_entry import DraftEntry
from models.journal_entry import JournalEntry
from services.backups import active_book_id


def test_stdio_draft_types_permissions_reconnect_and_revocation(client_id, accounts, tmp_path):
    vault = tmp_path / "fake-vault.json"
    config = {"book": str(dbc.DATABASE_PATH), "key": dbc.get_active_key(),
              "book_id": active_book_id(), "level": "propose", "export_roots": "[]"}

    def save_config():
        replacement = vault.with_suffix(".tmp")
        replacement.write_text(json.dumps(config))
        replacement.replace(vault)

    save_config()
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "helpers/mcp_stdio_worker.py"), str(vault)],
        env=dict(os.environ, LEDGERTB_DB_PATH=str(dbc.DATABASE_PATH), ANTHROPIC_API_KEY="test-key-never-used"),
    )
    proposed = {}

    async def exercise():
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"propose_entry", "list_drafts", "post_entry", "trial_balance"} <= names
                for entry_type in ("Regular", "Adjusting", "Beginning Balance"):
                    result = await session.call_tool("propose_entry", {
                        "client_id": client_id, "entry_date": "2026-01-01",
                        "entry_type": entry_type, "description": f"Stdio {entry_type}",
                        "lines": [{"account_number": "1000", "debit": 10},
                                  {"account_number": "3000", "credit": 10}],
                    })
                    assert not result.is_error, result
                    payload = json.loads(result.content[0].text)
                    proposed[entry_type] = payload["draft_id"]
                result = await session.call_tool("list_drafts", {"client_id": client_id})
                assert not result.is_error
                # MCP emits one text content block per list item.
                payload = [json.loads(block.text) for block in result.content]
                assert {d["entry_type"] for d in payload} == set(proposed)
                assert JournalEntry.count(client_id) == 0
                denied = await session.call_tool("post_entry", {
                    "client_id": client_id, "entry_date": "2026-01-01", "description": "Denied direct posting",
                    "lines": [{"account_number": "1000", "debit": 10}, {"account_number": "3000", "credit": 10}],
                })
                assert denied.is_error
                assert JournalEntry.count(client_id) == 0
                config["level"] = "read"
                save_config()
                denied = await session.call_tool("propose_entry", {
                    "client_id": client_id, "entry_date": "2026-01-01", "description": "Denied after downgrade",
                    "lines": [{"account_number": "1000", "debit": 10}, {"account_number": "3000", "credit": 10}],
                })
                assert denied.is_error
                assert DraftEntry.pending_count(client_id) == 3

        # Resolve in the human process, then reconnect the assistant.
        DraftEntry.get_by_id(proposed["Adjusting"], client_id).approve()
        DraftEntry.get_by_id(proposed["Regular"], client_id).reject()
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()
                result = await session.call_tool("list_drafts", {"client_id": client_id, "status": "all"})
                assert not result.is_error
                assert {(d["entry_type"], d["status"]) for d in
                        (json.loads(block.text) for block in result.content)} == {
                    ("Adjusting", "approved"), ("Regular", "rejected"), ("Beginning Balance", "pending"),
                }
                config["key"] = None
                save_config()
                denied = await session.call_tool("trial_balance", {"client_id": client_id})
                assert denied.is_error
                assert "not enabled" in denied.content[0].text

    asyncio.run(asyncio.wait_for(exercise(), timeout=60))
    proposals = [log for log in AuditLog.get_all(client_id)
                 if log.table_name == "draft_entries" and log.action == "INSERT"]
    assert len(proposals) == 3
    assert all("(AI)" in log.performed_by for log in proposals)
