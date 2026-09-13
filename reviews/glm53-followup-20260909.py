"""GLM-5.3 TokenHub exploratory replication: two canaries, then 48 staggered calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("base", HERE.with_name("deepseek-thinking-followup-20260909.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
OUT = base.ROOT / "artifacts/glm53-followup-20260909"
KEY = base.ROOT / "tokenhub.key"
ENDPOINT = "https://tokenhub.tencentmaas.com/v1/chat/completions"
SETTINGS = {"model": "glm-5.3", "thinking": {"type": "enabled"}, "reasoning_effort": "high",
            "max_tokens": 131072, "temperature": 1.0, "top_p": 0.95,
            "response_format": {"type": "json_object"}, "stream": False}
TIMEOUT = 3600
INTERVAL = 3


def plan():
    prior, tasks = base.checked_plan()
    files = [HERE, HERE.with_name("deepseek-thinking-followup-20260909.py"),
             base.ROOT / "artifacts/deepseek-thinking-128k-staggered-20260909/analysis.json",
             base.ROOT / "artifacts/deepseek-thinking-128k-staggered-20260909/case-audit.json"]
    p = {"kind": "glm53_exploratory_cross_model_followup", "created_utc": base.now(),
         "endpoint": ENDPOINT, "settings": SETTINGS, "timeout_seconds": TIMEOUT,
         "dispatch_interval_seconds": INTERVAL, "max_workers": 48,
         "canary_tasks": prior["canary_tasks"], "tasks": tasks, "benchmark_binding": prior["benchmark_binding"],
         "input_file_hashes": {str(f.relative_to(base.ROOT)): base.sha(f) for f in files},
         "maximum_calls": 50, "maximum_completion_tokens": 50 * 131072,
         "retry": False, "resume": False, "private_key_read": False,
         "key_source": "local tokenhub.key; secret never serialized or hashed",
         "canary_rule": "same two target-free tasks, both valid final choices, stop finish, nonempty reasoning, requested model returned",
         "failure_policy": "no retry: canary fail blocks benchmark; benchmark transport/envelope fail stops new submissions and drains submitted requests; invalid final choices consume slot",
         "evidence_scope": "exploratory_cross_model_on_fixed_development_constructed_worlds",
         "comparison": "same paired endpoints; DeepSeek disabled/thinking, 24 frozen policies and separately labelled 2 posthoc nonconstant diagnostics; no new inferential tests or pooled independent-world claims",
         "caveats": "high effort is not calibrated across vendors; GLM sampling explicitly 1.0/0.95 versus DeepSeek thinking sampling ignored; provider and model differ"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", p)
    print("GLM plan saved: 2 canaries, then 48 tasks if passed; high/128K, 3s stagger.", flush=True)


def checked_plan():
    p = base.read(OUT / "plan.json")
    prior, tasks = base.checked_plan()
    if (p["settings"] != SETTINGS or p["endpoint"] != ENDPOINT or p["timeout_seconds"] != TIMEOUT
        or p["dispatch_interval_seconds"] != INTERVAL or p["max_workers"] != 48
        or p["maximum_calls"] != 50 or p["tasks"] != tasks or p["canary_tasks"] != prior["canary_tasks"]
        or p["benchmark_binding"] != prior["benchmark_binding"]):
        raise ValueError("GLM plan mismatch")
    for f, expected in p["input_file_hashes"].items():
        if base.sha(base.ROOT / f) != expected:
            raise ValueError("bound input changed")
    return p


def request_body(task):
    # No sample-curl system message: retain exact experimental prompt contract.
    return {**SETTINGS, "messages": [{"role": "user", "content": task["rendered_prompt"]}]}


def response_record(task, payload, elapsed):
    expression, inp, out, model, finish, hit, miss, reasoning, fmt, fingerprint = base._extract_response(payload)
    if model != SETTINGS["model"]:
        raise ValueError("unexpected provider model")
    response = base.GenerationResponse(expression=expression, input_tokens=inp, output_tokens=out,
        latency_ms=elapsed, provider_model=model, finish_reason=finish, prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss, reasoning_tokens=reasoning, candidate_format=fmt,
        provider_fingerprint=fingerprint, seed_supported=False)
    r = base.live._response_record(task_id=task["task_id"], prompt_sha256=task["prompt_sha256"],
        opaque_option_ids=base.live._option_ids_from_prompt(task["rendered_prompt"]), response=response)
    text = payload["choices"][0]["message"].get("reasoning_content")
    r["thinking_telemetry"] = {"reasoning_content_present": isinstance(text, str) and bool(text.strip()),
        "reasoning_character_count": len(text) if isinstance(text, str) else None,
        "reasoning_content_sha256": hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None,
        "output_truncated": finish == "length"}
    return r


def read_key():
    if KEY.is_symlink() or not KEY.is_file() or KEY.stat().st_mode & 0o077:
        raise ValueError("credential must be a local regular file with mode 0600")
    key = KEY.read_text().strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("credential file must contain one token")
    return key


def run(execute):
    if not execute:
        raise ValueError("requires --execute")
    p = checked_plan()
    key = read_key()
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    lock = threading.Lock()
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        plan_sha = base.sha(OUT / "plan.json")
        event({"event": "start", "plan_file_sha256": plan_sha})
        for phase, tasks in (("canary", p["canary_tasks"]), ("generation", p["tasks"])):
            records, failures, dispatched = {}, [], []
            stop = threading.Event()
            def worker(index):
                task = tasks[index]
                with lock:
                    event({"event": "attempt_started", "phase": phase, "index": index, "task_id": task["task_id"]})
                start = time.monotonic()
                try:
                    response = base.UrllibHTTPTransport().post(url=ENDPOINT,
                        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                        body=json.dumps(request_body(task)).encode(), timeout=TIMEOUT)
                    if response.status != 200:
                        raise base.HTTPStatusError(response.status)
                    r = response_record(task, json.loads(response.body), (time.monotonic() - start) * 1000)
                    with lock:
                        base.save(OUT / f"{phase}-response-{index:02d}.json", r)
                        event({"event": "response", "phase": phase, "index": index, "record": r})
                        records[index] = r
                        print(f"{phase} saved {len(records)}/{len(tasks)}; task={index + 1}; valid={r['valid_choice']}; finish={r['response']['finish_reason']}", flush=True)
                except Exception as exc:
                    stop.set()
                    failure = {"index": index, "error_type": type(exc).__name__,
                               "http_status": getattr(exc, "status_code", None),
                               "transport_category": getattr(exc, "category", None)}
                    with lock:
                        failures.append(failure)
                        event({"event": "failure", "phase": phase, **failure})
                        print(f"{phase} request failed: {type(exc).__name__}; no retry, draining active requests.", flush=True)
            with ThreadPoolExecutor(max_workers=48) as pool:
                futures = []
                for index in range(len(tasks)):
                    if index:
                        time.sleep(INTERVAL)
                    if stop.is_set():
                        break
                    with lock:
                        event({"event": "submitted", "phase": phase, "index": index})
                    dispatched.append(index)
                    futures.append(pool.submit(worker, index))
                for f in futures:
                    f.result()
            complete = not failures and len(records) == len(tasks)
            result = {"kind": "glm53_" + phase, "created_utc": base.now(), "complete": complete,
                      "plan_file_sha256": plan_sha, "records": [records[i] for i in sorted(records)],
                      "dispatched_indices": dispatched, "provider_calls": len(dispatched), "failures": failures,
                      "usage_complete": not failures,
                      "known_response_usage": {k: sum(r["response"][k] for r in records.values()) for k in ("input_tokens", "output_tokens")}}
            if phase == "canary":
                result["passed"] = complete and base.canary_pass(result["records"])
                result["evidence"] = False
            else:
                result["evidence_scope"] = p["evidence_scope"]
                result["unscored_model_responses"] = True
            base.save(OUT / (phase + ".json"), result)
            if not complete or (phase == "canary" and not result["passed"]):
                event({"event": "stopped", "phase": phase, "complete": complete})
                print("Stopped before further calls; retained all responses.", flush=True)
                return
            event({"event": "phase_complete", "phase": phase, "calls": len(dispatched)})
        event({"event": "complete", "calls": 50})
        print("GLM finished: 48 benchmark responses saved, ready for offline scoring.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan() if args.command == "plan" else run(args.execute)
