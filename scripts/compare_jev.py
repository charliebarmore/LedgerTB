"""Compare categorizers on labeled fictional evidence, never real books or vaults.

Cloud calls require --live-jev / --live-anthropic. The TypeSafe development key is
read without printing/exporting it. Every live invocation is a fresh paid run;
there are no automatic retries. Reports are checkpointed after each Jev batch.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVIEW_OUTCOMES = {"insufficient_information", "split_required", "transfer_review"}


def summarize(rows):
    valid = [r for r in rows if not r.get("error")]
    correct = sum(r["actual"] == r["expected"] for r in valid)
    wrong = [r for r in valid if r["actual"] != r["expected"]]
    accounts = [r for r in valid if r["actual"] not in REVIEW_OUTCOMES]
    return {
        "cases": len(rows), "completed": len(valid), "correct": correct,
        "correctness_all_cases": correct / len(rows) if rows else None,
        "abstentions": sum(r["actual"] in REVIEW_OUTCOMES for r in valid),
        "account_suggestions": len(accounts),
        "account_suggestion_precision": sum(r["actual"] == r["expected"] for r in accounts) / len(accounts) if accounts else None,
        "confident_errors": sum(r.get("confidence", 0) >= .8 for r in wrong),
        "wrong_account_suggestions": sum(r["actual"] not in REVIEW_OUTCOMES for r in wrong),
        "confident_wrong_account_suggestions": sum(r["actual"] not in REVIEW_OUTCOMES and r.get("confidence", 0) >= .8 for r in wrong),
        "missed_account_suggestions": sum(r["actual"] in REVIEW_OUTCOMES and r["expected"] not in REVIEW_OUTCOMES for r in wrong),
        "review_route_mismatches": sum(r["actual"] in REVIEW_OUTCOMES and r["expected"] in REVIEW_OUTCOMES for r in wrong),
        "errors": len(rows) - len(valid),
        "median_latency_seconds": statistics.median([r["latency_seconds"] for r in rows]) if rows else None,
    }


def describe_rows(rows):
    kinds = sorted({r["kind"] for r in rows})
    return {"summary": summarize(rows), "by_kind": {kind: summarize([r for r in rows if r["kind"] == kind]) for kind in kinds},
            "rows": rows}


def load_fixture(path, split):
    fixture = json.loads(path.read_text())
    if path.resolve() != (ROOT / "tests/fixtures/jev_comparison.json").resolve() and fixture.get("synthetic") is not True:
        raise ValueError("Only explicitly labeled synthetic fixtures are supported.")
    cases = fixture["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique.")
    allowed = REVIEW_OUTCOMES | {a["number"] for a in fixture["accounts"] if a["type"] in ("Expense", "Revenue")}
    for case in cases:
        if case["expected"] not in allowed:
            raise ValueError("A label is not an eligible account or review outcome.")
    selected = [c for c in cases if split == "all" or c.get("split") == split]
    if not selected:
        raise ValueError("No cases in the selected split.")
    return fixture, selected


def transaction_for(case, bank_account_id):
    # Labels, rationales, kind, split, fixture IDs and context selectors never enter state.
    return {"date": case.get("date", "2026-01-15"), "description": case["description"],
            "amount": case["amount"], "receipt_text": case.get("receipt_text", ""),
            "is_transfer": bool(case.get("is_transfer", False)), "bank_account_id": bank_account_id}


def business_context(fixture, case):
    return fixture["contexts"][case["context"]] if "contexts" in fixture else fixture["business_context"]


def labeled_row(case, actual, confidence, elapsed, error=None, **extra):
    return {"id": case["id"], "kind": case["kind"], "split": case.get("split", "smoke"),
            "expected": case["expected"], "actual": actual, "confidence": confidence,
            "error": error, "latency_seconds": elapsed, **extra}


def evaluate_jev(cases, transactions, accounts, client_id, fixture, *, api_key,
                 batch_size=25, max_requests=16, transport=None, progress=None):
    from services import jev_categorization as jev
    if not 1 <= batch_size <= jev.MAX_BATCH:
        raise ValueError(f"Batch size must be 1..{jev.MAX_BATCH}.")
    groups = defaultdict(list)
    for case, transaction in zip(cases, transactions, strict=True):
        data = jev.request_input(transaction, accounts, client_id, business_context(fixture, case))
        # Evaluation-only scope is stable and fictional; production always uses the actual ledger scope.
        fingerprint = jev.request_key(("synthetic-evaluation", client_id), data)
        groups[json.dumps((data["business_context"], data["eligible_accounts"]), sort_keys=True)].append((case, data, fingerprint))
    chunks = []
    for group in groups.values():
        for i in range(0, len(group), batch_size):
            chunk = group[i:i + batch_size]
            for planned, _ in jev.plan_requests({key: data for _, data, key in chunk}):
                chunks.append([entry for entry in chunk if entry[2] in planned])
    if len(chunks) > max_requests:
        raise ValueError("Request limit would be exceeded; select fewer cases or explicitly increase --max-requests.")
    rows, batches, cache = [], [], {}
    by_id = {a.id: str(a.account_number) for a in accounts}
    started = time.monotonic()
    for index, chunk in enumerate(chunks):
        payload_inputs = {key: data for _, data, key in chunk}
        called = []
        def send(payload, key):
            called.append(None)
            response = (transport or jev.send_request)(payload, key)
            called[-1] = response
            return response
        batch_start = time.monotonic()
        jev.suggest(payload_inputs, cache, api_key=api_key, consent=True, transport=send)
        duration = time.monotonic() - batch_start
        response = called[0] if called and isinstance(called[0], dict) else {}
        usage = response.get("usage", {})
        usage = {k: v for k, v in usage.items() if k in ("input_tokens", "output_tokens")
                 and type(v) is int and v >= 0} if isinstance(usage, dict) else {}
        batches.append({"index": index, "case_ids": [case["id"] for case, _, _ in chunk],
                        "network_requests": len(called), "latency_seconds": duration,
                        "model": response.get("model") if isinstance(response.get("model"), str) else None,
                        "usage": usage, "error": next((cache[k]["error"] for _, _, k in chunk if cache[k].get("error")), None)})
        if batches[-1]["error"] and response:
            # Fictional evaluation only: retain the typed answer fields needed
            # to diagnose validation failures, never credentials or HTTP bodies.
            batches[-1]["invalid_answers"] = response.get("answers")
        for case, _, key in chunk:
            result = cache[key]
            rows.append(labeled_row(case, by_id.get(result.get("account_id"), result.get("outcome")),
                                    result.get("confidence", 0), result.get("latency_seconds", duration),
                                    result.get("error"), probabilities=result.get("probabilities"), request_key=key,
                                    batch_index=index))
        if progress:
            progress(jev_report(rows, batches, time.monotonic() - started))
    return jev_report(rows, batches, time.monotonic() - started)


def jev_report(rows, batches, duration):
    return {**describe_rows(rows), "models": sorted({b["model"] for b in batches if b["model"]}),
            "elapsed_seconds": duration, "batches": batches,
            "network_requests": sum(b["network_requests"] for b in batches),
            "usage": {key: sum(b["usage"].get(key, 0) for b in batches) for key in ("input_tokens", "output_tokens")},
            "cost_usd": None, "cost_note": "API returns tokens, not billed dollars; usage is counted once per request. Row latencies describe their batch, not independent requests."}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(report, indent=2) + "\n")
    staging.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/jev_comparison.json")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--case-id", action="append", help="Explicit diagnostic subset within the selected split; repeat for more IDs")
    parser.add_argument("--live-jev", action="store_true")
    parser.add_argument("--live-anthropic", action="store_true")
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".typesafe.env")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-requests", type=int, default=16)
    parser.add_argument("--output", type=Path, default=ROOT / "output/jev-comparison.json")
    args = parser.parse_args()
    fixture, cases = load_fixture(args.fixture, args.split)
    if args.case_id:
        selected_ids = set(args.case_id)
        if selected_ids - {c["id"] for c in cases}:
            raise SystemExit("An explicit case ID is not in the selected split.")
        cases = [c for c in cases if c["id"] in selected_ids]
    # Patch BEFORE importing config; none of this program accesses the real vault.
    from utils import secure_store
    secure_store.get_secret = lambda name: None
    secure_store.set_secret = lambda *args: None
    secure_store.delete_secret = lambda *args: None
    secure_store.migrate_legacy_secret = lambda *args: None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "synthetic-evaluation-placeholder"
    with tempfile.TemporaryDirectory(prefix="ledgertb-jev-eval-") as scratch:
        os.environ["LEDGERTB_DB_PATH"] = str(Path(scratch) / "synthetic.db")
        os.environ["LEDGERTB_BACKUP_DIR"] = str(Path(scratch) / "backups")
        from database import connection as db
        from database.crypto import derive_key
        from models.account import Account
        from models.client import Client
        from services.pattern_learning import PatternLearner
        from services.categorization import CategorizationService
        from services import jev_categorization as jev
        from dotenv import dotenv_values
        if not db.ENCRYPTION_AVAILABLE:
            raise SystemExit("SQLCipher is required for the disposable benchmark.")
        db.set_active_key(derive_key("disposable-synthetic-passphrase"))
        db.init_database()
        client_id = Client(name="Synthetic Evaluation Studio", entity_type="S-Corp").save(seed_accounts=False)
        accounts = []
        for row in fixture["accounts"]:
            account = Account(client_id=client_id, account_number=row["number"], name=row["name"], type=row["type"])
            account.save()
            accounts.append(account)
        by_number = {str(a.account_number): a.id for a in accounts}
        by_id = {a.id: str(a.account_number) for a in accounts}
        for description, number in fixture["training_patterns"]:
            PatternLearner.learn_pattern(client_id, description, by_number[number])
        bank_account_id = next((a.id for a in accounts if a.type == "Asset"), None)
        transactions = [transaction_for(c, bank_account_id) for c in cases]
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        report = {"fixture": args.fixture.name, "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
                  "split": args.split, "case_count": len(cases), "source_base_commit": revision,
                  "jev_instructions_sha256": hashlib.sha256(jev.INSTRUCTIONS.encode()).hexdigest(),
                  "jev_service_sha256": hashlib.sha256((ROOT / "services/jev_categorization.py").read_bytes()).hexdigest(),
                  "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
                  "scope": "Assistant-labeled synthetic evaluation, not independent adjudication or production calibration.",
                  "confident_error_definition": "Wrong exact label with concentration/confidence >= 0.8; providers are not calibrated equivalents.",
                  "evidence_note": "Jev sees transaction description, receipt evidence, transfer flag and business context. Existing local rules match descriptions only. Existing Anthropic sees descriptions and business context, not separate receipt_text/transfer flags; it has no explicit split/transfer outcomes.",
                  "providers": {}}
        local_rows = []
        for case, transaction in zip(cases, transactions, strict=True):
            start = time.monotonic()
            result = PatternLearner.find_match(client_id, transaction["description"])
            local_rows.append(labeled_row(case, by_id[result["account_id"]] if result else "insufficient_information",
                                          result["confidence"] if result else 0, time.monotonic() - start))
        report["providers"]["local_patterns"] = {**describe_rows(local_rows), "usage": {"cost_usd": 0, "network_requests": 0}}
        write_report(args.output, report)
        if args.live_jev:
            key = dotenv_values(args.key_file).get("TYPESAFE_API_KEY")
            if not key:
                raise SystemExit("No development TypeSafe key found. No key value was printed.")
            def progress(partial):
                report["providers"]["jev"] = partial
                write_report(args.output, report)
            report["providers"]["jev"] = evaluate_jev(cases, transactions, accounts, client_id, fixture, api_key=key,
                batch_size=args.batch_size, max_requests=args.max_requests, progress=progress)
        else:
            report["providers"]["jev"] = {"status": "not run; use --live-jev"}
        if args.live_anthropic and anthropic_key:
            from anthropic import Anthropic
            service = CategorizationService()
            service.client = Anthropic(api_key=anthropic_key, timeout=30, max_retries=0)
            rows = []
            # Keep context scoped to the corresponding case; no receipt enrichment of the existing service.
            groups = defaultdict(list)
            for case, transaction in zip(cases, transactions, strict=True):
                groups[business_context(fixture, case)].append((case, transaction))
            if sum((len(group) + 24) // 25 for group in groups.values()) > args.max_requests:
                raise SystemExit("Anthropic request limit exceeded.")
            for context, group in groups.items():
                start = time.monotonic()
                service.categorize_transactions([t for _, t in group], jev.eligible_accounts(accounts, client_id), business_context=context)
                duration = time.monotonic() - start
                for c, t in group:
                    rows.append(labeled_row(c, by_id.get(t.get("suggested_account_id"), "insufficient_information"),
                        {"high": 1, "medium": .5, "low": 0}.get(t.get("confidence"), 0), duration,
                        "Anthropic request failed" if service.last_error else None))
            report["providers"]["anthropic"] = {**describe_rows(rows), "usage": None, "cost_usd": None,
                "note": "Existing service does not expose usage. Confidence mapping high=1/medium=.5/low=0 is comparison-only."}
        else:
            report["providers"]["anthropic"] = {"status": "not run; needs --live-anthropic and ANTHROPIC_API_KEY in environment"}
        db.clear_active_key()
    report["status"] = "complete"
    write_report(args.output, report)
    print(json.dumps({k: v.get("summary", v.get("status")) for k, v in report["providers"].items()}, indent=2))


if __name__ == "__main__":
    main()
