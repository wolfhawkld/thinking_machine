import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve()
REVIEWS = HERE.parent
if str(REVIEWS) not in sys.path:
    sys.path.insert(0, str(REVIEWS))

import new_evidence_response_audit_20260910 as audit


class SyntheticRun:
    """Build a complete terminal ledger without constructing an API client."""

    def __init__(self, retry_indices=(0,)):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / "out"
        self.out.mkdir(mode=0o700)
        self.plan_path = self.out / "plan.json"
        self.tasks = []
        for index in range(audit.TASK_COUNT):
            prompt = f"synthetic new-evidence prompt {index}"
            self.tasks.append(
                {
                    "task_id": f"TASK-{index:02d}",
                    "rendered_prompt": prompt,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                }
            )
        self.plan = {
            "kind": "new_evidence_candidate_law_live_v1",
            "settings": audit.live.SETTINGS,
            "endpoint": audit.live.transport.ENDPOINT,
            "formal_calls": audit.TASK_COUNT,
            "maximum_calls": audit.MAX_CALLS,
            "max_technical_retries": audit.MAX_TECHNICAL_RETRIES,
            "max_workers": audit.TASK_COUNT,
            "conditions": ["baseline", "new_evidence", "repeat_evidence"],
            "tasks": self.tasks,
        }
        self.retry_indices = tuple(sorted(retry_indices))
        self._write_json(self.plan_path, self.plan)
        self._build_artifacts()

    def close(self):
        self.temp.cleanup()

    def _write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)

    def _payload(self, index):
        return {
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"expression":"(const 0)"}',
                        "reasoning_content": "synthetic reasoning",
                    },
                }
            ],
            "usage": {"prompt_tokens": 10 + index, "completion_tokens": 20 + index},
        }

    def _failure(self):
        return {
            "category": "transport_error",
            "transport_category": "timeout",
            "delivery_ambiguous": True,
            "retryable": True,
            "stop_new_dispatch": False,
        }

    def _response(self, index, attempt, payload):
        task = self.tasks[index]
        return audit.live.record_response(task, payload, float(index + attempt))

    def _build_artifacts(self):
        failure = self._failure()
        events = [{"event": "start", "plan_file_sha256": audit.live.transport.sha(self.plan_path)}]
        finish_events = {}
        records = {}
        slot_failures = {}
        slot_raw_files = {}

        # The old transport logs every first-round submission and start before
        # it waits for the first wave to drain.  Keeping this synthetic ledger
        # in that shape exercises the same replay checks as a real run.
        for index, task in enumerate(self.tasks):
            events.append({"event": "submitted", "phase": "first_round", "index": index, "task_id": task["task_id"]})
            events.append({"event": "request_started", "index": index, "attempt": 1, "task_id": task["task_id"]})
        for index, task in enumerate(self.tasks):
            if index in self.retry_indices:
                finish = {
                    "index": index,
                    "attempt": 1,
                    "task_id": task["task_id"],
                    "status": "technical_failure",
                    "record": None,
                    "failure": failure,
                    "raw_response_file": None,
                }
                slot_failures[index] = [failure]
            else:
                payload = self._payload(index)
                raw_name = f"raw-response-{index:02d}-attempt-01.bin"
                (self.out / raw_name).write_bytes(json.dumps(payload).encode())
                (self.out / raw_name).chmod(0o600)
                record = self._response(index, 1, payload)
                records[index] = record
                slot_raw_files[index] = [raw_name]
                finish = {
                    "index": index,
                    "attempt": 1,
                    "task_id": task["task_id"],
                    "status": "response",
                    "record": record,
                    "failure": None,
                    "raw_response_file": raw_name,
                }
            finish_events[index, 1] = finish
            self._write_json(
                self.out / f"attempt-{index:02d}-01.json",
                {key: finish[key] for key in ("index", "task_id", "attempt", "status", "record", "failure", "raw_response_file")},
            )
            events.append({"event": "attempt_finished", **finish})

        # Retry events deliberately occur only after every first attempt has a
        # finish event, and are in ascending task-index order.
        for index in self.retry_indices:
            task = self.tasks[index]
            events.append({"event": "retry_scheduled", "index": index, "attempt": 2, "task_id": task["task_id"]})
            events.append({"event": "request_started", "index": index, "attempt": 2, "task_id": task["task_id"]})
            payload = self._payload(index)
            raw_name = f"raw-response-{index:02d}-attempt-02.bin"
            (self.out / raw_name).write_bytes(json.dumps(payload).encode())
            (self.out / raw_name).chmod(0o600)
            record = self._response(index, 2, payload)
            records[index] = record
            slot_raw_files[index] = [raw_name]
            finish = {
                "index": index,
                "attempt": 2,
                "task_id": task["task_id"],
                "status": "response",
                "record": record,
                "failure": None,
                "raw_response_file": raw_name,
            }
            finish_events[index, 2] = finish
            self._write_json(
                self.out / f"attempt-{index:02d}-02.json",
                {key: finish[key] for key in ("index", "task_id", "attempt", "status", "record", "failure", "raw_response_file")},
            )
            events.append({"event": "attempt_finished", **finish})

        slots = []
        generation_records = []
        generation_failures = []
        for index, task in enumerate(self.tasks):
            attempts = [attempt for (slot, attempt) in finish_events if slot == index]
            final = finish_events[index, max(attempts)]
            if index in self.retry_indices:
                generation_failures.append({"index": index, "task_id": task["task_id"], **failure})
            raw_files = []
            for attempt in attempts:
                raw_name = finish_events[index, attempt]["raw_response_file"]
                if raw_name is not None:
                    raw_files.append(raw_name)
            slot = {
                "index": index,
                "task_id": task["task_id"],
                "prompt_sha256": task["prompt_sha256"],
                "status": final["status"],
                "attempt_count": len(attempts),
                "technical_failures": slot_failures.get(index, []),
                "raw_response_files": raw_files,
                "record": final["record"],
            }
            slots.append(slot)
            if final["record"] is not None:
                generation_records.append(final["record"])

        physical = len(finish_events)
        generation = {
            "kind": "shortcut_challenge_deepseek_thinking_live",
            "plan_file_sha256": audit.live.transport.sha(self.plan_path),
            "complete": True,
            "evaluable": True,
            "formal_calls": audit.TASK_COUNT,
            "provider_calls": physical,
            "records": generation_records,
            "slots": slots,
            "attempt_count": physical,
            "attempt_count_by_slot": {str(slot["index"]): slot["attempt_count"] for slot in slots},
            "technical_failures": generation_failures,
            "known_response_usage": {
                "input_tokens": sum(r["response"]["input_tokens"] for r in generation_records),
                "output_tokens": sum(r["response"]["output_tokens"] for r in generation_records),
            },
            # Recovered retries still leave a technical failure in the audit,
            # matching the old transport's usage-complete definition.
            "usage_complete": not generation_failures,
            "valid_response_count": len(generation_records),
        }
        self._write_json(self.out / "generation.json", generation)
        events.append({"event": "complete", "complete": True, "provider_calls": physical,
                       "saved_record_count": len(generation_records)})
        ledger = self.out / "attempts.jsonl"
        ledger.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        ledger.chmod(0o600)


class NewEvidenceResponseAuditTests(unittest.TestCase):
    def setUp(self):
        self.run = SyntheticRun()
        self.patches = [
            mock.patch.object(audit.live, "OUT", self.run.out),
            mock.patch.object(audit.live, "PLAN_PATH", self.run.plan_path),
            mock.patch.object(audit.live, "validate", return_value=(self.run.plan, self.run.tasks)),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.run.close()

    def test_replays_new_record_not_old_option_parser_and_is_stable(self):
        calls = []
        actual = audit.live.record_response

        def replay(task, payload, latency):
            calls.append(task["task_id"])
            return actual(task, payload, latency)

        def forbidden(*args, **kwargs):
            raise AssertionError("historical opaque-option parser was used")

        with mock.patch.object(audit.live, "record_response", replay), mock.patch.object(
            audit.live.transport.base, "record_response", forbidden
        ):
            first = audit.audit()
            # The runner invokes audit before analysis.  A later analysis file
            # must not alter a repeat read-only audit result.
            (self.run.out / "analysis.json").write_text("{}", encoding="utf-8")
            (self.run.out / "analysis.json").chmod(0o600)
            second = audit.audit()
        self.assertEqual(first, second)
        self.assertEqual(first["task_slots"], 27)
        self.assertEqual(first["physical_attempts"], 28)
        self.assertEqual(first["technical_retries"], 1)
        self.assertEqual(first["raw_response_replays"], 27)
        self.assertEqual(len(calls), 54)

    def test_rejects_retry_before_first_wave_drains(self):
        events = [json.loads(line) for line in (self.run.out / "attempts.jsonl").read_text().splitlines()]
        retry_position = next(i for i, event in enumerate(events) if event["event"] == "retry_scheduled")
        first_finish = next(i for i, event in enumerate(events) if event["event"] == "attempt_finished")
        self.assertGreater(retry_position, first_finish)
        retry = events.pop(retry_position)
        events.insert(first_finish, retry)
        (self.run.out / "attempts.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        (self.run.out / "attempts.jsonl").chmod(0o600)
        with self.assertRaisesRegex(ValueError, "first wave drained"):
            audit.audit()

    def test_rejects_content_retry_and_wrong_lowest_index(self):
        # A response (including an invalid candidate response) is never an
        # eligible technical retry.  Exercise the same closed policy helper
        # directly so no synthetic API payload or private test data is needed.
        tasks = self.run.tasks
        events = [
            {"event": "start", "plan_file_sha256": "x"},
            {"event": "submitted", "phase": "first_round", "index": 0, "task_id": tasks[0]["task_id"]},
            {"event": "request_started", "index": 0, "attempt": 1, "task_id": tasks[0]["task_id"]},
            {"event": "attempt_finished", "index": 0, "attempt": 1, "task_id": tasks[0]["task_id"],
             "status": "response", "record": {"content": "bad", "valid_choice": False},
             "failure": None, "raw_response_file": "raw-response-00-attempt-01.bin"},
            {"event": "retry_scheduled", "index": 0, "attempt": 2, "task_id": tasks[0]["task_id"]},
            {"event": "request_started", "index": 0, "attempt": 2, "task_id": tasks[0]["task_id"]},
            {"event": "attempt_finished", "index": 0, "attempt": 2, "task_id": tasks[0]["task_id"],
             "status": "response", "record": {"content": "bad", "valid_choice": False},
             "failure": None, "raw_response_file": "raw-response-00-attempt-02.bin"},
            {"event": "complete"},
        ]
        starts = [e for e in events if e["event"] == "request_started"]
        finishes = [e for e in events if e["event"] == "attempt_finished"]
        with self.assertRaisesRegex(ValueError, "lowest-index eligible|justified"):
            audit._validate_dispatch_and_retries(events, starts, finishes, tasks)


if __name__ == "__main__":
    unittest.main()
