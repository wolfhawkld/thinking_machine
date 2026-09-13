"""Bounded post-primary thinking follow-up. No changes to the frozen live runner.

Commands: plan; run --execute; analyze. One attempt per slot, no retry/resume.
Only public rendered prompts reach the provider. Scoring is a separate command.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import spark_strong_k4_utilization_primary_live as live
from src.providers.openai_compatible import UrllibHTTPTransport, _extract_response, HTTPStatusError
from src.runner import GenerationResponse

OUT = ROOT / "artifacts/deepseek-thinking-followup-20260909"
OLD = ROOT / "artifacts/spark-strong-k4-utilization-primary-live-v1-20260908"
ENDPOINT = "https://api.deepseek.com/chat/completions"
SETTINGS = {"model": "deepseek-v4-pro", "thinking": {"type": "enabled"},
            "reasoning_effort": "high", "max_tokens": 8192,
            "response_format": {"type": "json_object"}, "stream": False}
TIMEOUT = 300


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    # Exclusive create: no existing experimental result is overwritten.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def public_inputs():
    binding = live.load_frozen_config()["benchmark_binding"]
    path = ROOT / binding["public_manifest_relative_path"]
    if sha(path) != binding["public_manifest_file_sha256"]:
        raise ValueError("public manifest binding mismatch")
    tasks = read(path)["tasks"]
    if len(tasks) != 48 or len({t["task_id"] for t in tasks}) != 48:
        raise ValueError("requires exactly 48 distinct public tasks")
    for task in tasks:
        if hashlib.sha256(task["rendered_prompt"].encode()).hexdigest() != task["prompt_sha256"]:
            raise ValueError("prompt hash mismatch")
    return binding, tasks


def plan():
    binding, tasks = public_inputs()
    if sha(OLD / "plan.json") != "f8d99fba3c50510beb6180c2f14fc34d91fed30ee0ef61caff1c923ee07641de":
        raise ValueError("old plan binding mismatch")
    pair = read(OLD / "plan.json")["target_free_paired_canary"]["pairs"][0]
    canary = [{k: pair["arms"][arm][k] for k in ("task_id", "rendered_prompt", "prompt_sha256")}
              for arm in pair["condition_order"]]
    files = [Path(__file__), ROOT / "src/providers/openai_compatible.py",
             ROOT / "src/runner.py", ROOT / "src/spark_strong_k4_utilization_primary_live.py",
             ROOT / "reviews/utilization-supplement-20260908.py", OLD / "plan.json",
             OLD / "generation.json", OLD / "analysis.json"]
    result = {"kind": "post_primary_thinking_configuration_followup", "created_utc": now(),
              "evidence_scope": "descriptive_outcome_conditioned_development_only",
              "settings": SETTINGS, "endpoint": ENDPOINT, "timeout_seconds": TIMEOUT,
              "temperature": "omitted: ignored by provider in thinking mode",
              "benchmark_binding": binding, "canary_tasks": canary,
              "task_schedule": [{k: t[k] for k in ("task_id", "prompt_sha256")} for t in tasks],
              "input_file_hashes": {str(p.relative_to(ROOT)): sha(p) for p in files},
              "canary_calls": 2, "formal_calls": 48, "maximum_calls": 50,
              "maximum_completion_tokens_all_calls": 50 * SETTINGS["max_tokens"],
              "retry": False, "resume": False, "sequential_stateless": True,
              "canary_pass": "both valid listed choices, stop finish, nonempty reasoning, same model",
              "failure_policy": "canary failure stops; HTTP/transport/envelope failure stops non-evaluable; invalid final choice consumes slot and paired world is tie/miss",
              "analysis": "frozen paired scoring; all 24 baselines; old/new paired complete-switch table; no new inferential tests",
              "comparison_caveat": "thinking, effective sampling, token cap and timeout change together; same worlds are not new independent samples"}
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    save(OUT / "plan.json", result)
    print("Plan saved; maximum 2 + 48 calls; completion-token cap 409600.", flush=True)


def checked_plan():
    result = read(OUT / "plan.json")
    if result["settings"] != SETTINGS or result["endpoint"] != ENDPOINT or result["timeout_seconds"] != TIMEOUT:
        raise ValueError("follow-up settings drift")
    for path, expected in result["input_file_hashes"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("bound input changed: " + path)
    binding, tasks = public_inputs()
    if binding != result["benchmark_binding"] or result["task_schedule"] != [
        {k: t[k] for k in ("task_id", "prompt_sha256")} for t in tasks
    ]:
        raise ValueError("benchmark or schedule drift")
    return result, tasks


def request_body(task):
    return {**SETTINGS, "messages": [{"role": "user", "content": task["rendered_prompt"]}]}


def record_response(task, payload, elapsed):
    expression, inp, out, model, finish, hit, miss, reasoning, fmt, fingerprint = _extract_response(payload)
    if model != SETTINGS["model"]:
        raise ValueError("unexpected response model")
    response = GenerationResponse(expression=expression, input_tokens=inp, output_tokens=out,
                                  latency_ms=elapsed, provider_model=model, finish_reason=finish,
                                  prompt_cache_hit_tokens=hit, prompt_cache_miss_tokens=miss,
                                  reasoning_tokens=reasoning, candidate_format=fmt,
                                  provider_fingerprint=fingerprint, seed_supported=False)
    record = live._response_record(task_id=task["task_id"], prompt_sha256=task["prompt_sha256"],
                                   opaque_option_ids=live._option_ids_from_prompt(task["rendered_prompt"]),
                                   response=response)
    text = payload["choices"][0]["message"].get("reasoning_content")
    record["thinking_telemetry"] = {"reasoning_content_present": isinstance(text, str) and bool(text.strip()),
                                    "reasoning_character_count": len(text) if isinstance(text, str) else None,
                                    "reasoning_content_sha256": hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None,
                                    "output_truncated": finish == "length"}
    return record


def canary_pass(records):
    return len(records) == 2 and all(r["valid_choice"] and r["response"]["finish_reason"] == "stop"
                                    and r["thinking_telemetry"]["reasoning_content_present"] for r in records)


def run(execute):
    if not execute:
        raise ValueError("real calls require --execute")
    plan_data, tasks = checked_plan()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is absent")
    # Exclusive ledger prevents accidental retry/resume, including an interrupted request.
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            ledger.write(json.dumps({"utc": now(), **value}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        event({"event": "start", "plan_file_sha256": sha(OUT / "plan.json")})
        transport = UrllibHTTPTransport()
        for phase, schedule in (("canary", plan_data["canary_tasks"]), ("generation", tasks)):
            records = []
            for index, task in enumerate(schedule):
                event({"event": "attempt_started", "phase": phase, "index": index, "task_id": task["task_id"]})
                start = time.monotonic()
                try:
                    response = transport.post(url=ENDPOINT,
                                              headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                                              body=json.dumps(request_body(task)).encode(), timeout=TIMEOUT)
                    if response.status != 200:
                        raise HTTPStatusError(response.status)
                    payload = json.loads(response.body)
                    record = record_response(task, payload, (time.monotonic() - start) * 1000)
                except Exception as exc:
                    failure = {"phase": phase, "index": index, "error_type": type(exc).__name__,
                               "http_status": getattr(exc, "status_code", None), "complete": False,
                               "evaluable": False, "usage_complete": False,
                               "plan_file_sha256": sha(OUT / "plan.json"), "records": records}
                    event({"event": "failure", **{k: v for k, v in failure.items() if k != "records"}})
                    save(OUT / "failure.json", failure)
                    print(f"Stopped: {phase} slot {index + 1}, {type(exc).__name__}; no retry.", flush=True)
                    return
                records.append(record)
                event({"event": "response", "phase": phase, "index": index, "record": record})
                print(f"{phase} {index + 1}/{len(schedule)} saved; valid={record['valid_choice']}; finish={record['response']['finish_reason']}; output_tokens={record['response']['output_tokens']}", flush=True)
            result = {"kind": "thinking_followup_" + phase, "complete": True, "records": records,
                      "plan_file_sha256": sha(OUT / "plan.json"), "created_utc": now()}
            if phase == "canary":
                result["passed"] = canary_pass(records)
            save(OUT / (phase + ".json"), result)
            if phase == "canary" and not result["passed"]:
                print("Canary failed; no benchmark calls.", flush=True)
                return
        event({"event": "complete", "calls": 50})


def analyze():
    plan_data, tasks = checked_plan()
    canary = read(OUT / "canary.json")
    generation = read(OUT / "generation.json")
    if not canary["passed"] or not canary_pass(canary["records"]) or not generation["complete"]:
        raise ValueError("requires passed canary and complete generation")
    for bundle in (canary, generation):
        if bundle["plan_file_sha256"] != sha(OUT / "plan.json"):
            raise ValueError("plan mismatch")
    records = generation["records"]
    if [{k: r[k] for k in ("task_id", "prompt_sha256")} for r in records] != plan_data["task_schedule"]:
        raise ValueError("generation schedule mismatch")
    binding = plan_data["benchmark_binding"]
    private_path = ROOT / binding["private_key_relative_path"]
    if sha(private_path) != binding["private_key_file_sha256"]:
        raise ValueError("private key mismatch")
    private = read(private_path)
    rows, totals, strata = live._score_private_pairs(private, {r["task_id"]: r for r in records})
    old_rows = read(OLD / "analysis.json")["pair_results"]
    old = {r["pair_ordinal"]: r for r in old_rows}
    comparison = Counter(thinking_only=0, disabled_only=0, both=0, neither=0)
    for row in rows:
        a = row["complete_two_arm_context_concordant_switch"]
        b = old[row["pair_ordinal"]]["complete_two_arm_context_concordant_switch"]
        comparison["both" if a and b else "thinking_only" if a else "disabled_only" if b else "neither"] += 1
    spec = importlib.util.spec_from_file_location("supplement", ROOT / "reviews/utilization-supplement-20260908.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = {"kind": "post_primary_thinking_descriptive_analysis", "evidence_scope": plan_data["evidence_scope"],
              "new_inferential_tests": False, "generation_file_sha256": sha(OUT / "generation.json"),
              "plan_file_sha256": sha(OUT / "plan.json"), "totals": totals, "strata": strata,
              "paired_valid_own_hits": sum(r["own_context_correct_count"] for r in rows),
              "paired_valid_cross_hits": sum(r["cross_context_correct_count"] for r in rows),
              "valid_calls": sum(r["valid_choice"] for r in records),
              "truncated_calls": sum(r["thinking_telemetry"]["output_truncated"] for r in records),
              "reasoning_present_calls": sum(r["thinking_telemetry"]["reasoning_content_present"] for r in records),
              "usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")},
              "old_new_complete_switch": dict(comparison), "pair_results": rows,
              "all_baseline_comparisons": module.summarize(private, rows)}
    save(OUT / "analysis.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("pair_results", "all_baseline_comparisons")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "analyze"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    {"plan": plan, "run": lambda: run(args.execute), "analyze": analyze}[args.command]()
