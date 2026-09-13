import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from src.providers.openai_compatible import HTTPResponse, TransportError


HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("shortcut_challenge_live", HERE.with_name("shortcut_challenge_live_20260910.py"))
live = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(live)


class SyntheticTransport:
    """A no-network transport whose behavior is keyed by rendered prompt."""

    behaviors = {}
    calls = []
    lock = threading.Lock()

    def post(self, *, url, headers, body, timeout):
        payload = json.loads(body)
        prompt = payload["messages"][0]["content"]
        with self.lock:
            ordinal = len(self.calls)
            self.calls.append(prompt)
            actions = self.behaviors.setdefault(prompt, [])
            action = actions.pop(0) if actions else "valid"
        if isinstance(action, BaseException):
            raise action
        option = live.base.live._option_ids_from_prompt(prompt)[0]
        content = "not-json" if action == "invalid" else json.dumps({"expression": option})
        return HTTPResponse(
            status=action if isinstance(action, int) else 200,
            body=json.dumps({
                "model": "deepseek-v4-pro",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            }).encode(),
        )


class ShortcutChallengeLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, public_tasks = live.base.public_inputs()
        cls.tasks = [
            {key: task[key] for key in ("task_id", "rendered_prompt", "prompt_sha256")}
            for task in public_tasks[:18]
        ]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.temp.name) / "plan.json"
        self.out = Path(self.temp.name) / "out"
        plan = {
            "settings": live.SETTINGS,
            "endpoint": live.ENDPOINT,
            "timeout_seconds": 3600,
            "tasks": self.tasks,
            "input_file_hashes": {
                "reviews/shortcut_challenge_live_20260910.py": live.sha(live.HERE),
            },
            "maximum_calls": 20,
            "formal_calls": 18,
            "max_technical_retries": 2,
            "interval_seconds": 3,
        }
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        SyntheticTransport.behaviors = {}
        SyntheticTransport.calls = []
        self.patches = [
            mock.patch.object(live, "PLAN_PATH", self.plan_path),
            mock.patch.object(live, "OUT", self.out),
            mock.patch.object(live.base, "UrllibHTTPTransport", SyntheticTransport),
            mock.patch.object(live.time, "sleep", return_value=None),
            mock.patch.dict(live.os.environ, {"DEEPSEEK_API_KEY": "synthetic-key"}),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def run_live(self):
        return live.run(True)

    def test_invalid_200_consumes_slot_without_retry(self):
        SyntheticTransport.behaviors[self.tasks[0]["rendered_prompt"]] = ["invalid"]
        result = self.run_live()
        self.assertEqual(result["provider_calls"], 18)
        self.assertEqual(len(result["records"]), 18)
        self.assertEqual(result["slots"][0]["status"], "response")
        self.assertFalse(result["slots"][0]["record"]["valid_choice"])
        self.assertEqual(result["slots"][0]["attempt_count"], 1)
        self.assertTrue(result["complete"])
        self.assertFalse(result["evaluable"])
        self.assertEqual(len(list(self.out.glob("raw-response-*.bin"))), 18)
        self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in self.out.glob("raw-response-*.bin")))
        ledger_events = [json.loads(line) for line in (self.out / "attempts.jsonl").read_text().splitlines()]
        finished = [event for event in ledger_events if event["event"] == "attempt_finished"]
        self.assertEqual(len(finished), 18)
        self.assertTrue(all("record" in event and "raw_response_file" in event for event in finished))
        self.assertEqual(len(list(self.out.glob("attempt-*.json"))), 18)

    def test_two_technical_retries_are_budgeted_and_saved(self):
        for task in self.tasks:
            SyntheticTransport.behaviors[task["rendered_prompt"]] = [
                TransportError(category="timeout", delivery_ambiguous=True)
            ]
        result = self.run_live()
        self.assertEqual(result["provider_calls"], 20)
        self.assertEqual(result["attempt_count"], 20)
        self.assertEqual(result["slots"][0]["attempt_count"], 2)
        self.assertEqual(result["slots"][1]["attempt_count"], 2)
        self.assertEqual(result["slots"][2]["attempt_count"], 1)
        # Only the two preregistered retries recover; the remaining technical
        # failures stay explicit in slots and are retained in the denominator.
        self.assertEqual(len(result["records"]), 2)
        self.assertFalse(result["usage_complete"])
        self.assertEqual(len(result["technical_failures"]), 18)
        attempt_files = list(self.out.glob("attempt-*.json"))
        self.assertEqual(len(attempt_files), 20)
        self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in attempt_files))
        self.assertEqual(len(result["slots"][0]["raw_response_files"]), 1)
        self.assertEqual(len(result["slots"][1]["raw_response_files"]), 1)

    def test_retry_preserves_first_response_body_and_stops_after_protocol_error(self):
        first = self.tasks[0]["rendered_prompt"]
        second = self.tasks[1]["rendered_prompt"]
        SyntheticTransport.behaviors[first] = [500, 401]
        SyntheticTransport.behaviors[second] = [TransportError(category="timeout", delivery_ambiguous=True)]
        result = self.run_live()
        self.assertEqual(result["provider_calls"], 19)
        self.assertEqual(result["slots"][0]["status"], "technical_failure")
        self.assertEqual(result["slots"][0]["attempt_count"], 2)
        self.assertEqual(len(result["slots"][0]["raw_response_files"]), 2)
        # The 401 on the first recovery attempt blocks the second recovery.
        self.assertEqual(result["slots"][1]["attempt_count"], 1)
        self.assertEqual(result["slots"][1]["status"], "technical_failure")
        self.assertFalse(result["usage_complete"])


if __name__ == "__main__":
    unittest.main()
