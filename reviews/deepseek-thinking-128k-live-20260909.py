"""48-call post-primary thinking follow-up; reuse passed 128K canary, no retry."""
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("thinking_base", HERE.with_name("deepseek-thinking-followup-20260909.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
OUT = base.ROOT / "artifacts/deepseek-thinking-128k-live-20260909"
SETTINGS = {**base.SETTINGS, "max_tokens": 131072}
TIMEOUT = 3600
CANARY = base.ROOT / "artifacts/deepseek-thinking-128k-canary-20260909"


def plan():
    old, tasks = base.checked_plan()
    canary = base.read(CANARY / "canary.json")
    cp = base.read(CANARY / "plan.json")
    if (not canary["complete"] or not canary["passed"] or not base.canary_pass(canary["records"])
        or canary["plan_file_sha256"] != base.sha(CANARY / "plan.json")
        or cp["settings"] != SETTINGS or cp["timeout_seconds"] != TIMEOUT):
        raise ValueError("requires completed matching 128K canary")
    result = {"kind": "post_primary_thinking_128k_followup", "created_utc": base.now(),
              "settings": SETTINGS, "timeout_seconds": TIMEOUT, "endpoint": base.ENDPOINT,
              "tasks": tasks, "maximum_calls": 48, "benchmark_calls": 48,
              "maximum_completion_tokens": 48 * 131072, "retry": False, "resume": False,
              "private_key_read": False, "evidence_scope": "descriptive_outcome_conditioned_development_only",
              "task_schedule": [{k: t[k] for k in ("task_id", "prompt_sha256")} for t in tasks],
              "benchmark_binding": old["benchmark_binding"],
              "source_file_sha256": base.sha(HERE),
              "previous_plan_file_sha256": base.sha(base.OUT / "plan.json"),
              "canary_plan_file_sha256": base.sha(CANARY / "plan.json"),
              "canary_file_sha256": base.sha(CANARY / "canary.json"),
              "failure_policy": "stop non-evaluable on transport/envelope error; no retry/resume/replacement; invalid arm makes paired world tie/miss",
              "analysis": "descriptive frozen paired endpoints, four strata, all 24 baselines and old/new complete switch; no new inferential tests",
              "comparison_caveat": "post-outcome configuration selected using technical canary; not old primary, not new independent worlds; thinking/sampling/budget/timeout differ"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", result)
    print("128K follow-up plan saved: 48 calls; no repeat canary.", flush=True)


def checked_plan():
    old, tasks = base.checked_plan()
    p = base.read(OUT / "plan.json")
    c = base.read(CANARY / "canary.json")
    if (p["source_file_sha256"] != base.sha(HERE) or p["settings"] != SETTINGS
        or p["timeout_seconds"] != TIMEOUT or p["tasks"] != tasks
        or p["endpoint"] != base.ENDPOINT or p["maximum_calls"] != 48 or p["benchmark_calls"] != 48
        or p["benchmark_binding"] != old["benchmark_binding"]
        or p["task_schedule"] != [{k: t[k] for k in ("task_id", "prompt_sha256")} for t in tasks]
        or p["previous_plan_file_sha256"] != base.sha(base.OUT / "plan.json")
        or p["canary_plan_file_sha256"] != base.sha(CANARY / "plan.json")
        or p["canary_file_sha256"] != base.sha(CANARY / "canary.json")
        or not c["complete"] or not c["passed"] or not base.canary_pass(c["records"])):
        raise ValueError("128K follow-up plan or bound inputs changed")
    return p


def request_body(task):
    return {**SETTINGS, "messages": [{"role": "user", "content": task["rendered_prompt"]}]}


def run(execute):
    if not execute:
        raise ValueError("requires --execute")
    p = checked_plan()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY absent")
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        plan_sha = base.sha(OUT / "plan.json")
        event({"event": "start", "plan_file_sha256": plan_sha})
        records = []
        transport = base.UrllibHTTPTransport()
        for index, task in enumerate(p["tasks"]):
            event({"event": "attempt_started", "index": index, "task_id": task["task_id"]})
            start = time.monotonic()
            try:
                response = transport.post(url=base.ENDPOINT,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    body=json.dumps(request_body(task)).encode(), timeout=TIMEOUT)
                if response.status != 200:
                    raise base.HTTPStatusError(response.status)
                record = base.record_response(task, json.loads(response.body), (time.monotonic() - start) * 1000)
            except Exception as exc:
                failure = {"complete": False, "evidence": False, "benchmark_calls": index + 1,
                           "plan_file_sha256": plan_sha, "index": index, "records": records,
                           "error_type": type(exc).__name__, "http_status": getattr(exc, "status_code", None),
                           "transport_category": getattr(exc, "category", None), "usage_complete": False}
                event({"event": "failure", **{k: v for k, v in failure.items() if k != "records"}})
                base.save(OUT / "failure.json", failure)
                print("Stopped; no retry: " + type(exc).__name__, flush=True)
                return
            records.append(record)
            event({"event": "response", "index": index, "record": record})
            print(f"generation {index + 1}/48 saved; valid={record['valid_choice']}; finish={record['response']['finish_reason']}; output_tokens={record['response']['output_tokens']}", flush=True)
        result = {"kind": p["kind"], "complete": True, "unscored_model_responses": True,
                  "created_utc": base.now(), "plan_file_sha256": plan_sha, "records": records,
                  "evidence_scope": p["evidence_scope"], "benchmark_calls": 48, "provider_calls": 48,
                  "usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")}}
        base.save(OUT / "generation.json", result)
        event({"event": "complete", "calls": 48})
        print("Finished: 48 calls saved; ready for separate analysis.", flush=True)


def analyze():
    plan_data = checked_plan()
    canary = base.read(CANARY / "canary.json")
    generation = base.read(OUT / "generation.json")
    if not canary["passed"] or not base.canary_pass(canary["records"]) or not generation["complete"]:
        raise ValueError("requires passed canary and complete generation")
    for bundle in (generation,):
        if bundle["plan_file_sha256"] != base.sha(OUT / "plan.json"):
            raise ValueError("plan mismatch")
    records = generation["records"]
    if [{k: r[k] for k in ("task_id", "prompt_sha256")} for r in records] != plan_data["task_schedule"]:
        raise ValueError("generation schedule mismatch")
    binding = plan_data["benchmark_binding"]
    private_path = base.ROOT / binding["private_key_relative_path"]
    if base.sha(private_path) != binding["private_key_file_sha256"]:
        raise ValueError("private key mismatch")
    private = base.read(private_path)
    rows, totals, strata = base.live._score_private_pairs(private, {r["task_id"]: r for r in records})
    old_rows = base.read(base.OLD / "analysis.json")["pair_results"]
    old = {r["pair_ordinal"]: r for r in old_rows}
    comparison = Counter(thinking_only=0, disabled_only=0, both=0, neither=0)
    for row in rows:
        a = row["complete_two_arm_context_concordant_switch"]
        b = old[row["pair_ordinal"]]["complete_two_arm_context_concordant_switch"]
        comparison["both" if a and b else "thinking_only" if a else "disabled_only" if b else "neither"] += 1
    spec = importlib.util.spec_from_file_location("supplement", base.ROOT / "reviews/utilization-supplement-20260908.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = {"kind": "post_primary_thinking_descriptive_analysis", "evidence_scope": plan_data["evidence_scope"],
              "new_inferential_tests": False, "generation_file_sha256": base.sha(OUT / "generation.json"),
              "plan_file_sha256": base.sha(OUT / "plan.json"), "totals": totals, "strata": strata,
              "paired_valid_own_hits": sum(r["own_context_correct_count"] for r in rows),
              "paired_valid_cross_hits": sum(r["cross_context_correct_count"] for r in rows),
              "valid_calls": sum(r["valid_choice"] for r in records),
              "truncated_calls": sum(r["thinking_telemetry"]["output_truncated"] for r in records),
              "reasoning_present_calls": sum(r["thinking_telemetry"]["reasoning_content_present"] for r in records),
              "usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")},
              "old_new_complete_switch": dict(comparison), "pair_results": rows,
              "all_baseline_comparisons": module.summarize(private, rows)}
    base.save(OUT / "analysis.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("pair_results", "all_baseline_comparisons")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "analyze"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    {"plan": plan, "run": lambda: run(args.execute), "analyze": analyze}[args.command]()
