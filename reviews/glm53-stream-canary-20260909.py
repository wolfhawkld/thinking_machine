"""Observable two-task streaming canary; no benchmark calls and no retries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("glm", HERE.with_name("glm53-followup-20260909.py"))
glm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(glm)
base = glm.base
OUT = base.ROOT / "artifacts/glm53-stream-canary-20260909"
SETTINGS = {**glm.SETTINGS, "stream": True, "stream_options": {"include_usage": True}}
TOTAL_TIMEOUT = 3600
NO_PROGRESS_TIMEOUT = 120
PROGRESS_INTERVAL = 30


def plan():
    old = glm.checked_plan()
    ledger = glm.OUT / "attempts.jsonl"
    events = [json.loads(s) for s in ledger.read_text().splitlines()]
    starts = [e for e in events if e["event"] == "attempt_started"]
    if len(starts) != 2 or any(e["event"] == "response" for e in events) or (glm.OUT / "generation.json").exists():
        raise ValueError("unexpected prior state; inspect before migration")
    p = {"kind": "glm53_streaming_technical_canary", "created_utc": base.now(),
         "settings": SETTINGS, "endpoint": glm.ENDPOINT, "tasks": old["canary_tasks"],
         "hard_total_timeout_seconds": TOTAL_TIMEOUT, "no_semantic_progress_timeout_seconds": NO_PROGRESS_TIMEOUT,
         "progress_interval_seconds": PROGRESS_INTERVAL, "dispatch_interval_seconds": 3,
         "maximum_calls": 2, "maximum_completion_tokens": 262144, "benchmark_calls": 0,
         "prior_process_exit": 143, "prior_interrupted_calls": 2, "prior_usage": "unknown, server cancellation not guaranteed",
         "automatic_retry": False, "automatic_resume": False, "evidence": False,
         "source_file_sha256": base.sha(HERE), "old_plan_file_sha256": base.sha(glm.OUT / "plan.json"),
         "old_ledger_file_sha256": base.sha(ledger),
         "pass_rule": "both return requested model, nonempty reasoning, valid final option, stop finish, usage, DONE and successful curl exit",
         "privacy": "no raw reasoning, credential or HTTP auth logged; final normalized selection and counts/hashes/usage saved"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", p)
    print("Streaming plan saved: exactly two canaries; high/128K, 120s no-progress timeout, 3600s hard deadline.", flush=True)


def checked_plan():
    old = glm.checked_plan()
    p = base.read(OUT / "plan.json")
    if (p["source_file_sha256"] != base.sha(HERE) or p["settings"] != SETTINGS
        or p["tasks"] != old["canary_tasks"] or p["endpoint"] != glm.ENDPOINT
        or p["old_plan_file_sha256"] != base.sha(glm.OUT / "plan.json")
        or p["old_ledger_file_sha256"] != base.sha(glm.OUT / "attempts.jsonl")
        or p["hard_total_timeout_seconds"] != TOTAL_TIMEOUT
        or p["no_semantic_progress_timeout_seconds"] != NO_PROGRESS_TIMEOUT):
        raise ValueError("streaming plan drift")
    return p


class StreamState:
    def __init__(self):
        self.reasoning_count = 0
        self.reasoning_hash = hashlib.sha256()
        self.answer = ""
        self.events = 0
        self.done = False
        self.model = None
        self.finish = None
        self.usage = None
        self.fingerprint = None

    def feed(self, line):
        if not line.startswith("data:"):
            return False
        data = line[5:].strip()
        if data == "[DONE]":
            self.done = True
            return False
        d = json.loads(data)
        if "error" in d:
            raise ValueError("provider SSE error")
        self.events += 1
        model = d.get("model")
        if model:
            if model != SETTINGS["model"]:
                raise ValueError("unexpected streaming model")
            self.model = model
        if d.get("system_fingerprint"): self.fingerprint = d["system_fingerprint"]
        if d.get("usage"): self.usage = d["usage"]
        progressed = False
        for c in d.get("choices", []):
            if c.get("index", 0) != 0:
                raise ValueError("unexpected multiple choices")
            delta = c.get("delta", {})
            reasoning, content = delta.get("reasoning_content") or "", delta.get("content") or ""
            self.reasoning_count += len(reasoning)
            self.reasoning_hash.update(reasoning.encode())
            self.answer += content
            if len(self.answer) > 1048576:
                raise ValueError("excessive final content")
            progressed |= bool(reasoning or content)
            if c.get("finish_reason"): self.finish = c["finish_reason"]
        return progressed

    def record(self, task, elapsed):
        if not self.done or self.model is None or self.finish is None or self.usage is None:
            raise ValueError("incomplete stream envelope")
        payload = {"model": self.model, "usage": self.usage,
                   "choices": [{"finish_reason": self.finish, "message": {"content": self.answer}}]}
        if self.fingerprint: payload["system_fingerprint"] = self.fingerprint
        r = glm.response_record(task, payload, elapsed * 1000)
        r["thinking_telemetry"] = {"reasoning_content_present": self.reasoning_count > 0,
            "reasoning_character_count": self.reasoning_count,
            "reasoning_content_sha256": self.reasoning_hash.hexdigest(),
            "output_truncated": self.finish == "length"}
        return r


def request(task, key, notify):
    body = {**SETTINGS, "messages": [{"role": "user", "content": task["rendered_prompt"]}]}
    cfg = 'url = ' + json.dumps(glm.ENDPOINT) + '\nrequest = "POST"\nheader = ' + json.dumps('Authorization: Bearer ' + key)
    cfg += '\nheader = "Content-Type: application/json"\ndata = ' + json.dumps(json.dumps(body)) + '\n'
    p = subprocess.Popen(['curl', '--config', '-', '--silent', '--show-error', '--no-buffer',
        '--dump-header', '-', '--connect-timeout', '15', '--max-time', str(TOTAL_TIMEOUT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start = last_progress = last_report = time.monotonic()
    state = StreamState()
    buffer = b""
    headers = True
    http_status = None
    first_byte = first_sse = None
    failure = None
    try:
        p.stdin.write(cfg.encode()); p.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout, selectors.EVENT_READ)
            while True:
                now = time.monotonic()
                if now-start >= TOTAL_TIMEOUT:
                    failure = "hard_total_timeout"; break
                if now-last_progress >= NO_PROGRESS_TIMEOUT and not state.done:
                    failure = "no_reasoning_or_answer_progress_120s"; break
                if now-last_report >= PROGRESS_INTERVAL:
                    notify({"event": "progress", "elapsed_seconds": round(now-start, 2),
                            "reasoning_characters": state.reasoning_count, "answer_characters": len(state.answer),
                            "sse_events": state.events, "idle_seconds": round(now-last_progress, 2)})
                    last_report = now
                if not selector.select(timeout=1):
                    continue
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk: break
                if first_byte is None:
                    first_byte = round(time.monotonic()-start, 2)
                    notify({"event": "first_byte", "elapsed_seconds": first_byte})
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8").rstrip("\r")
                    if line.startswith("HTTP/"):
                        http_status = int(line.split()[1]); headers = True
                        notify({"event": "http_status", "status": http_status})
                        continue
                    if headers:
                        if not line: headers = False
                        continue
                    before = state.events
                    if state.feed(line): last_progress = time.monotonic()
                    if state.events > before and first_sse is None:
                        first_sse = round(time.monotonic()-start, 2)
                        notify({"event": "first_sse", "elapsed_seconds": first_sse})
        if failure is None and buffer.strip():
            raise ValueError("incomplete trailing stream data")
    except Exception as exc:
        failure = type(exc).__name__  # Never retain provider bodies or key-bearing exceptions.
    finally:
        if failure is None and p.poll() is None:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: failure = "curl_exit_timeout"
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
    telemetry = {"http_status": http_status, "curl_exit": p.returncode,
                 "elapsed_seconds": round(time.monotonic()-start, 2), "first_byte_seconds": first_byte,
                 "first_sse_seconds": first_sse, "reasoning_characters": state.reasoning_count,
                 "answer_characters": len(state.answer), "sse_events": state.events,
                 "done": state.done, "finish_reason": state.finish, "reported_usage": state.usage}
    p.stderr.close(); p.stdout.close()
    if failure is None and (p.returncode != 0 or http_status != 200):
        failure = "curl_or_http_failure"
    record = None
    if failure is None:
        try: record = state.record(task, telemetry["elapsed_seconds"])
        except Exception as exc: failure = type(exc).__name__
    return {"record": record, "failure": failure, "telemetry": telemetry}


def run(execute):
    if not execute: raise ValueError("requires --execute")
    plan = checked_plan()
    key = glm.read_key()
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    lock = threading.Lock()
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            with lock:
                ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
                ledger.flush(); os.fsync(ledger.fileno())
                if value["event"] in ("first_sse", "response", "failure"):
                    print(json.dumps(value), flush=True)
        event({"event": "start", "plan_file_sha256": base.sha(OUT / "plan.json")})
        def worker(i):
            event({"event": "attempt_started", "index": i, "task_id": plan["tasks"][i]["task_id"]})
            result = request(plan["tasks"][i], key, lambda data: event({"index": i, **data}))
            base.save(OUT / f"response-{i}.json", result)
            event({"event": "failure" if result["failure"] else "response", "index": i,
                   "failure": result["failure"], "telemetry": result["telemetry"]})
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            f0 = pool.submit(worker, 0)
            time.sleep(3)
            f1 = pool.submit(worker, 1)
            results = [f0.result(), f1.result()]
        records = [r["record"] for r in results if r["record"] is not None]
        passed = all(r["failure"] is None for r in results) and base.canary_pass(records)
        summary = {"kind": plan["kind"], "passed": passed, "results": results, "records": records,
                   "plan_file_sha256": base.sha(OUT / "plan.json"), "provider_calls": 2,
                   "benchmark_calls": 0, "evidence": False, "created_utc": base.now()}
        base.save(OUT / "canary.json", summary)
        event({"event": "complete", "passed": passed, "benchmark_calls": 0})
        print(f"Streaming canary finished: passed={passed}; benchmark not started.", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan() if args.command == 'plan' else run(args.execute)
