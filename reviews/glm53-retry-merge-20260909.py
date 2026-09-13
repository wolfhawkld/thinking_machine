"""One user-authorized retry of index24; preserve all47 saved responses exactly."""
import argparse
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("live", HERE.with_name("glm53-stream-live-20260909.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
base = live.base
OUT = base.ROOT / "artifacts/glm53-retry-merge-20260909"
INDEX = 24


def original():
    p = live.checked_plan()
    g = base.read(live.OUT / "failure.json")
    if (g["complete"] is not False or g["provider_calls"] != 48
        or g["plan_file_sha256"] != base.sha(live.OUT / "plan.json")
        or len(g["results"]) != 48 or len(g["records"]) != 47):
        raise ValueError("unexpected original bundle")
    records = []
    for i, r in enumerate(g["results"]):
        if r != base.read(live.OUT / f"response-{i:02d}.json"):
            raise ValueError("per-task response mismatch")
        if i == INDEX:
            if r["failure"] != "no_reasoning_or_answer_progress_120s" or r["record"] is not None:
                raise ValueError("retry target was not the single missing response")
        else:
            if r["failure"] is not None or r["record"] is None:
                raise ValueError("unexpected additional failure")
            if any(r["record"][k] != p["tasks"][i][k] for k in ("task_id", "prompt_sha256")):
                raise ValueError("task/response binding mismatch")
            records.append(r["record"])
    if records != g["records"]:
        raise ValueError("original aggregate mismatch")
    return p, g


def plan():
    old, g = original()
    p = {"kind": "glm53_single_retry_merge_amendment", "created_utc": base.now(),
         "authorization": "user requested retry of sole failed task and merge with saved results",
         "retry_index_zero_based": INDEX, "task": old["tasks"][INDEX],
         "settings": old["settings"], "hard_total_timeout_seconds": old["hard_total_timeout_seconds"],
         "no_progress_timeout_seconds": old["no_progress_timeout_seconds"],
         "maximum_new_calls": 1, "maximum_new_completion_tokens": 131072,
         "prior_task_attempts": 48, "maximum_combined_task_attempts": 49,
         "automatic_retry": False, "private_key_read": False,
         "evidence_scope": "exploratory_cross_model_with_disclosed_single_missing_response_retry",
         "old_failure_file_sha256": base.sha(live.OUT / "failure.json"),
         "old_plan_file_sha256": base.sha(live.OUT / "plan.json"),
         "old_ledger_file_sha256": base.sha(live.OUT / "attempts.jsonl"),
         "source_file_sha256": base.sha(HERE), "live_source_file_sha256": base.sha(live.HERE),
         "selection_rule": "retry only absent final response, never based on correctness; preserve all47 others byte-equivalent as JSON values",
         "total_usage_complete": False, "prior_failed_request_usage": "unknown, not zero"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", p)
    print("One retry frozen: task25 only; old47 responses preserved.", flush=True)


def checked_plan():
    old, g = original()
    p = base.read(OUT / "plan.json")
    if (p["source_file_sha256"] != base.sha(HERE) or p["live_source_file_sha256"] != base.sha(live.HERE)
        or p["old_failure_file_sha256"] != base.sha(live.OUT / "failure.json")
        or p["old_plan_file_sha256"] != base.sha(live.OUT / "plan.json")
        or p["old_ledger_file_sha256"] != base.sha(live.OUT / "attempts.jsonl")
        or p["settings"] != old["settings"] or p["task"] != old["tasks"][INDEX]
        or p["retry_index_zero_based"] != INDEX or p["maximum_new_calls"] != 1
        or p["hard_total_timeout_seconds"] != live.stream.TOTAL_TIMEOUT
        or p["no_progress_timeout_seconds"] != live.stream.NO_PROGRESS_TIMEOUT):
        raise ValueError("retry plan drift")
    return p, old, g


def merge(old, g, retry):
    if retry["failure"] is not None or retry["record"] is None:
        raise ValueError("cannot merge failed retry")
    if any(retry["record"][k] != old["tasks"][INDEX][k] for k in ("task_id", "prompt_sha256")):
        raise ValueError("retry task mismatch")
    results = list(g["results"])
    results[INDEX] = retry
    records = [r["record"] for r in results]
    if len({r["task_id"] for r in records}) != 48:
        raise ValueError("merge duplicates or misses tasks")
    for i, r in enumerate(records):
        if any(r[k] != old["tasks"][i][k] for k in ("task_id", "prompt_sha256")):
            raise ValueError("merge order mismatch")
        if i != INDEX and r != g["results"][i]["record"]:
            raise ValueError("preserved record changed")
    return results, records


def run(execute):
    if not execute: raise ValueError("requires --execute")
    p, old, g = checked_plan()
    key = live.stream.glm.read_key()
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
            ledger.flush(); os.fsync(ledger.fileno())
            if value["event"] in ("first_sse", "complete", "failure"):
                print(json.dumps(value), flush=True)
        event({"event": "attempt_started", "index": INDEX, "task_id": p["task"]["task_id"],
               "plan_file_sha256": base.sha(OUT / "plan.json")})
        try:
            retry = live.stream.request(p["task"], key, event)
        except Exception as exc:
            retry = {"record": None, "failure": type(exc).__name__, "telemetry": {}}
        base.save(OUT / "retry-response.json", retry)
        if retry["failure"] is not None:
            event({"event": "failure", "failure": retry["failure"], "telemetry": retry["telemetry"]})
            print("Retry failed; no more attempts; old47 remain saved.", flush=True)
            return
        results, records = merge(old, g, retry)
        summary = {"kind": p["kind"], "complete": True, "unscored_model_responses": True,
                   "evidence_scope": p["evidence_scope"], "plan_file_sha256": base.sha(OUT / "plan.json"),
                   "old_failure_file_sha256": p["old_failure_file_sha256"],
                   "retry_response_file_sha256": base.sha(OUT / "retry-response.json"),
                   "results": results, "records": records, "retained_original_response_count": 47,
                   "retried_index_zero_based": INDEX, "known_task_physical_attempts": 49,
                   "usage_complete": False,
                   "known_response_usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")},
                   "created_utc": base.now()}
        base.save(OUT / "generation.json", summary)
        event({"event": "complete", "merged_responses": 48, "new_calls": 1,
               "valid_retry_choice": retry["record"]["valid_choice"]})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan() if args.command == 'plan' else run(args.execute)
