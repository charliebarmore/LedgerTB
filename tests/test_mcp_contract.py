"""Generated MCP schemas should document the inputs assistants must send."""

import asyncio
import json

import mcp_server


def _tool(name):
    tools = asyncio.run(mcp_server.server.list_tools())
    return next(tool for tool in tools if tool.name == name)


def test_import_accounts_schema_documents_each_row_field():
    schema = _tool("import_accounts").input_schema
    row_schema = schema["properties"]["rows"]["items"]
    if "$ref" in row_schema:
        row_schema = schema["$defs"][row_schema["$ref"].split("/")[-1]]

    assert set(row_schema["properties"]) >= {
        "number", "name", "type", "subtype", "description",
    }
    assert not row_schema.get("required")


def test_new_mcp_contract_inputs_are_discoverable():
    account_schema = _tool("list_accounts").input_schema["properties"]
    export_schema = _tool("export_close_package").input_schema
    create_schema = _tool("create_client").input_schema["properties"]

    assert "account_number" in account_schema
    assert "out_dir" not in export_schema.get("required", [])
    assert "initial_fiscal_year" in create_schema
    assert _tool("ensure_fiscal_year")


def test_import_accounts_typed_rows_preserve_per_row_results(
    db, client_id, monkeypatch
):
    monkeypatch.setattr(mcp_server, "_require_level", lambda _level: None)

    result = asyncio.run(mcp_server.server.call_tool("import_accounts", {
        "client_id": client_id,
        "rows": [
            {"number": 7777, "name": "Contract Asset", "type": "Asset",
             "detail_type": "Other Asset", "qbo_metadata": "preserved"},
            {"number": "7788", "type": "Asset"},
        ],
    }))
    payload = json.loads(result.content[0].text)

    assert payload["created"] == 1
    assert payload["errors"] and "rows[2]" in payload["errors"][0]
    created = mcp_server.mcp_tools.list_accounts(client_id, "7777")[0]
    assert created["subtype"] == "Other Asset"
