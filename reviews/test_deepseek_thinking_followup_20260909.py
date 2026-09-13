import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("thinking", Path(__file__).with_name("deepseek-thinking-followup-20260909.py"))
thinking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thinking)


class ThinkingFollowupTests(unittest.TestCase):
    def setUp(self):
        _, tasks = thinking.public_inputs()
        self.task = tasks[0]
        self.option = thinking.live._option_ids_from_prompt(self.task["rendered_prompt"])[0]

    def payload(self, content=None, finish="stop", reasoning="synthetic reasoning"):
        return {"model": "deepseek-v4-pro", "choices": [{"finish_reason": finish,
                "message": {"content": content if content is not None else json.dumps({"expression": self.option}),
                            "reasoning_content": reasoning}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 50,
                          "completion_tokens_details": {"reasoning_tokens": 40}}}

    def test_public_only_request(self):
        body = thinking.request_body({**self.task, "secret_target": "must not appear"})
        self.assertEqual(body["messages"], [{"role": "user", "content": self.task["rendered_prompt"]}])
        self.assertNotIn("temperature", body)
        self.assertNotIn("must not appear", json.dumps(body))
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["max_tokens"], 8192)

    def test_valid_reasoning_record(self):
        record = thinking.record_response(self.task, self.payload(), 10)
        self.assertTrue(record["valid_choice"])
        self.assertTrue(thinking.canary_pass([record, record]))
        self.assertEqual(record["response"]["reasoning_tokens"], 40)
        self.assertNotIn("synthetic reasoning", json.dumps(record))

    def test_truncation_consumes_slot_and_fails_canary(self):
        record = thinking.record_response(self.task, self.payload(content="", finish="length"), 10)
        self.assertFalse(record["valid_choice"])
        self.assertTrue(record["thinking_telemetry"]["output_truncated"])
        self.assertFalse(thinking.canary_pass([record, record]))

    def test_missing_reasoning_fails_canary(self):
        record = thinking.record_response(self.task, self.payload(reasoning=None), 10)
        self.assertFalse(thinking.canary_pass([record, record]))

    def test_wrong_model_rejected(self):
        payload = self.payload()
        payload["model"] = "other-model"
        with self.assertRaises(ValueError):
            thinking.record_response(self.task, payload, 10)

    def test_exclusive_results(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            thinking.save(path, {"first": True})
            with self.assertRaises(FileExistsError):
                thinking.save(path, {"second": True})
            self.assertEqual(thinking.read(path), {"first": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
