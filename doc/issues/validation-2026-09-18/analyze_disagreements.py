"""Classify archived disagreements; optionally replay the 1,789 historical passes.

Run from the repository root with PYTHONPATH=src .venv/bin/python <this file>.
Add --replay after building lean-server-worker to exercise the current verifier.
The original archive is never modified; replay uses local subprocesses, not HTTP.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE / "data"
TARGET = "axle_failed__local_passed"


def classify(row):
    reasons = set()
    for error in row["axle"].get("tool_errors", []):
        if "not in the allowed set of standard axioms" in error:
            reasons.add("axiom_policy")
        elif "does not match expected signature" in error:
            reasons.add("signature_mismatch")
        elif "Missing required declaration" in error:
            reasons.add("missing_declaration")
        else:
            reasons.add("other_tool_error")
    for error in row["axle"].get("lean_errors", []):
        if error.startswith("failed to compile formal_statement:"):
            reasons.add("formal_statement_compile_error")
        elif "No theorem or definition found in formal_statement" in error:
            reasons.add("no_formal_declaration")
        elif "validation error for LeanProblem" in error:
            reasons.add("input_validation_error")
        elif "worker timeout" in error:
            reasons.add("axle_timeout")
        else:
            reasons.add("other_lean_error")
    return "+".join(sorted(reasons)) or "unclassified"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def record_final_fingerprints(metadata):
    """Make concurrent edits visible instead of attributing results to later code."""
    metadata["fingerprints_checked_at"] = datetime.now(timezone.utc).isoformat()
    metadata["source_sha256_after"] = {name: sha256(ROOT / name) for name in metadata["source_sha256"]}
    metadata["changed_source_files"] = [
        name for name, digest in metadata["source_sha256"].items()
        if metadata["source_sha256_after"][name] != digest
    ]
    metadata["worker_sha256_after"] = sha256(ROOT / ".lake/build/bin/lean-server-worker")
    metadata["worker_binary_changed"] = metadata["worker_sha256_after"] != metadata["worker_sha256"]
    write_json(DATA / "strict-replay-metadata.json", metadata)


def analyze(rows):
    target = [r for r in rows if r["category"] == TARGET]
    groups = defaultdict(list)
    axiom_kinds = Counter()
    details = []
    for row in target:
        reason = classify(row)
        groups[reason].append(row)
        names = re.findall(r"Axiom '([^']+)'", "\n".join(row["axle"].get("tool_errors", [])))
        axiom_kind = None
        if names:
            axiom_kind = (
                "native_decide" if all("._native.native_decide." in n for n in names)
                else "decide_native" if all("._native.decide." in n for n in names)
                else "custom_axioms"
            )
            axiom_kinds[axiom_kind] += 1
        details.append({
            "uuid": row["uuid"], "line": row["line"], "data_source": row["data_source"],
            "reason": reason, "axiom_kind": axiom_kind,
        })
    with (DATA / "axle-failed-local-passed-classification.jsonl").open("w") as out:
        for item in details:
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
    summary = {
        "archive_sha256": sha256(DATA / "disagreements.jsonl"),
        "disagreements": len(rows),
        "categories": dict(Counter(r["category"] for r in rows)),
        "target_count": len(target),
        "target_unique_uuids": len({r["uuid"] for r in target}),
        "exclusive_reasons": {k: len(v) for k, v in sorted(groups.items())},
        "axiom_policy_subtypes_including_overlap": dict(axiom_kinds),
        "examples": {
            k: [r["uuid"] for r in sorted(v, key=lambda r: len(r["candidate"]))[:3]]
            for k, v in sorted(groups.items())
        },
    }
    write_json(DATA / "axle-failed-local-passed-analysis.json", summary)
    print(json.dumps(summary["exclusive_reasons"], indent=2), flush=True)
    return target


async def replay(rows, workers, timeout):
    from lean_server.protocol import VerifyWorkerRequest
    from lean_server.workers.process import WorkerProcessBackend

    binary = ROOT / ".lake/build/bin/lean-server-worker"
    tracked_sources = sorted((ROOT / "lean/LeanServerWorker").glob("*.lean"))
    tracked_sources += [ROOT / "src/lean_server/http.py", ROOT / "src/lean_server/protocol.py",
                        ROOT / "src/lean_server/workers/process.py"]
    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "local worker protocol v2; same acceptance criteria as HTTP verify_proof",
        "use_def_eq": True, "workers": workers, "timeout_seconds": timeout,
        "archive_sha256": sha256(DATA / "disagreements.jsonl"),
        "worker_sha256": sha256(binary),
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in tracked_sources},
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "note": "Working tree contains uncommitted changes; source hashes identify the tested files.",
    }
    write_json(DATA / "strict-replay-metadata.json", metadata)
    queue = asyncio.Queue()
    for row in rows:
        queue.put_nowait(row)
    results = []
    started = time.monotonic()
    with (DATA / "strict-replay.jsonl").open("w") as output:
        async def run_worker(index):
            backend = None
            try:
                while not queue.empty():
                    row = queue.get_nowait()
                    entry = {"uuid": row["uuid"], "line": row["line"], "reason": classify(row)}
                    request_started = time.monotonic()
                    try:
                        if backend is None:
                            backend = WorkerProcessBackend(["lake", "env", str(binary)], cwd=ROOT)
                            await backend.start()
                        request = VerifyWorkerRequest(row["uuid"], row["formal_statement"], row["candidate"])
                        result = await asyncio.wait_for(backend.compile(request), timeout)
                        if result.status == "internal_error":
                            outcome = "internal_error"
                        elif result.status == "ok" and not (result.errors or result.tool_errors or result.failed_declarations):
                            outcome = "passed"
                        else:
                            outcome = "rejected"
                        entry.update(outcome=outcome, result=asdict(result))
                    except Exception as exc:
                        entry.update(outcome="timeout" if isinstance(exc, TimeoutError) else "infrastructure_error",
                                     error_type=type(exc).__name__, error=str(exc))
                        if backend is not None:
                            await backend.close()
                            backend = None
                    entry["elapsed_seconds"] = round(time.monotonic() - request_started, 3)
                    output.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    output.flush()
                    results.append(entry)
                    if len(results) % 50 == 0 or entry["outcome"] != "rejected":
                        counts = dict(Counter(r["outcome"] for r in results))
                        print(f'{len(results)}/{len(rows)} {counts} elapsed={time.monotonic()-started:.0f}s', flush=True)
            finally:
                if backend is not None:
                    await backend.close()
        await asyncio.gather(*(run_worker(i) for i in range(workers)))
    by_reason = defaultdict(Counter)
    for row in results:
        by_reason[row["reason"]][row["outcome"]] += 1
    summary = {
        "count": len(results), "outcomes": dict(Counter(r["outcome"] for r in results)),
        "by_reason": {k: dict(v) for k, v in sorted(by_reason.items())},
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "non_rejections": [{k: r[k] for k in ("uuid", "reason", "outcome")} for r in results if r["outcome"] != "rejected"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(DATA / "strict-replay-summary.json", summary)
    record_final_fingerprints(metadata)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.workers < 1 or args.timeout <= 0:
        parser.error("workers and timeout must be positive")
    rows = [json.loads(line) for line in (DATA / "disagreements.jsonl").open()]
    target = analyze(rows)
    if args.replay:
        asyncio.run(replay(target, args.workers, args.timeout))


if __name__ == "__main__":
    main()
