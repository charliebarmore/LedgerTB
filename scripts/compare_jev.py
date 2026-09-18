"""Synthetic categorizer comparison; disposable encrypted DB and fake vault only.

No cloud calls unless --live-jev / --live-anthropic are explicit. Jev's development
key is read from --key-file without exporting or printing it. Never use client data.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize(rows):
    valid = [r for r in rows if not r.get("error")]
    return {
        "cases": len(rows), "completed": len(valid),
        "correct": sum(r["actual"] == r["expected"] for r in valid),
        "correctness_all_cases": sum(r["actual"] == r["expected"] for r in valid) / len(rows),
        "abstentions": sum(r["actual"] in ("insufficient_information", "split_required", "transfer_review") for r in valid),
        "confident_errors": sum(r["actual"] != r["expected"] and r.get("confidence", 0) >= .8 for r in valid),
        "errors": len(rows) - len(valid),
        "median_latency_seconds": statistics.median([r["latency_seconds"] for r in rows]) if rows else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-jev", action="store_true")
    parser.add_argument("--live-anthropic", action="store_true")
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".typesafe.env")
    parser.add_argument("--output", type=Path, default=ROOT / "output/jev-comparison.json")
    args = parser.parse_args()
    # Patch the vault BEFORE importing config, which otherwise reads the real vault.
    from utils import secure_store
    secure_store.get_secret = lambda name: None
    secure_store.set_secret = lambda *args: None
    secure_store.delete_secret = lambda *args: None
    secure_store.migrate_legacy_secret = lambda *args: None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "synthetic-evaluation-placeholder"
    from database import connection as db
    from database.crypto import derive_key
    from models.account import Account
    from models.client import Client
    from services.pattern_learning import PatternLearner
    from services.categorization import CategorizationService
    from services import jev_categorization as jev
    from dotenv import dotenv_values
    fixture = json.loads((ROOT / "tests/fixtures/jev_comparison.json").read_text())
    report = {"fixture": "tests/fixtures/jev_comparison.json", "scope": "Synthetic smoke comparison, not production calibration",
              "confident_error_definition": "Wrong labeled outcome with concentration/confidence >= 0.8; metrics across providers are not calibrated equivalents.",
              "providers": {}}
    if not db.ENCRYPTION_AVAILABLE:
        raise SystemExit("SQLCipher is required for the disposable benchmark.")
    with tempfile.TemporaryDirectory(prefix="ledgertb-jev-eval-") as scratch:
        db.DATABASE_PATH = Path(scratch) / "synthetic.db"
        db.set_active_key(derive_key("disposable-synthetic-passphrase"))
        db.init_database()
        client_id = Client(name="Synthetic Cedar Design", entity_type="S-Corp").save(seed_accounts=False)
        accounts = []
        for row in fixture["accounts"]:
            account = Account(client_id=client_id, account_number=row["number"], name=row["name"], type=row["type"])
            account.save()
            accounts.append(account)
        by_number = {a.account_number: a.id for a in accounts}
        by_id = {a.id: a.account_number for a in accounts}
        for description, number in fixture["training_patterns"]:
            PatternLearner.learn_pattern(client_id, description, by_number[number])
        transactions = [{"date": "2026-01-15", "description": c["description"], "amount": c["amount"]} for c in fixture["cases"]]
        local_rows = []
        for case, transaction in zip(fixture["cases"], transactions):
            start = time.monotonic()
            result = PatternLearner.find_match(client_id, transaction["description"])
            local_rows.append({"id": case["id"], "kind": case["kind"], "expected": case["expected"],
                               "actual": by_id[result["account_id"]] if result else "insufficient_information",
                               "confidence": result["confidence"] if result else 0,
                               "latency_seconds": time.monotonic() - start})
        report["providers"]["local_patterns"] = {"summary": summarize(local_rows), "rows": local_rows,
                                                  "usage": {"cost_usd": 0, "network_requests": 0}}
        if args.live_jev:
            key = dotenv_values(args.key_file).get("TYPESAFE_API_KEY")
            if not key:
                raise SystemExit("No development TypeSafe key found. No key value was printed.")
            cache, batch, keys = {}, {}, []
            for transaction in transactions:
                data = jev.request_input(transaction, accounts, client_id, fixture["business_context"])
                fingerprint = jev.request_key((str(db.DATABASE_PATH), client_id), data)
                batch[fingerprint] = data
                keys.append(fingerprint)
            start = time.monotonic()
            jev.suggest(batch, cache, api_key=key, consent=True)
            duration = time.monotonic() - start
            rows = []
            for case, fingerprint in zip(fixture["cases"], keys):
                result = cache[fingerprint]
                rows.append({"id": case["id"], "kind": case["kind"], "expected": case["expected"],
                             "actual": by_id.get(result.get("account_id"), result.get("outcome")),
                             "confidence": result.get("confidence", 0), "error": result.get("error"),
                             "latency_seconds": result.get("latency_seconds", duration),
                             "probabilities": result.get("probabilities")})
            first = cache[keys[0]]
            report["providers"]["jev"] = {"summary": summarize(rows), "rows": rows,
                "model": first.get("model"), "batch_latency_seconds": duration,
                "usage": first.get("batch_usage", {}), "cost_usd": None,
                "cost_note": "API response exposes tokens, not billed dollars. No price assumed.", "network_requests": 1}
        else:
            report["providers"]["jev"] = {"status": "not run; use --live-jev"}
        if args.live_anthropic and anthropic_key:
            from anthropic import Anthropic
            service = CategorizationService()
            service.client = Anthropic(api_key=anthropic_key, timeout=30, max_retries=0)
            start = time.monotonic()
            service.categorize_transactions(transactions, accounts, business_context=fixture["business_context"])
            duration = time.monotonic() - start
            rows = [{"id": c["id"], "kind": c["kind"], "expected": c["expected"],
                     "actual": by_id.get(t.get("suggested_account_id"), "insufficient_information"),
                     "confidence": {"high": 1, "medium": .5, "low": 0}.get(t.get("confidence"), 0),
                     "error": "Anthropic request failed" if service.last_error else None,
                     "latency_seconds": duration} for c,t in zip(fixture["cases"], transactions)]
            report["providers"]["anthropic"] = {"summary": summarize(rows), "rows": rows,
                "batch_latency_seconds": duration, "usage": None, "cost_usd": None,
                "note": "Existing service does not expose token usage; categorical confidence mapped high=1/medium=.5/low=0 only for this comparison."}
        else:
            report["providers"]["anthropic"] = {"status": "not run; needs --live-anthropic and ANTHROPIC_API_KEY in environment"}
        db.clear_active_key()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v.get("summary", v.get("status")) for k,v in report["providers"].items()}, indent=2))


if __name__ == "__main__":
    main()
