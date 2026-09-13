import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("thinking_live", Path(__file__).with_name("deepseek-thinking-128k-live-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LiveTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "OUT", Path(d)), contextlib.redirect_stdout(io.StringIO()):
            m.plan()
            p = m.checked_plan()
            seen = []
            class Fake:
                def post(self, **kw):
                    body = json.loads(kw["body"])
                    seen.append(body)
                    if fail:
                        raise m.base.HTTPStatusError(503)
                    assert body["max_tokens"] == 131072 and kw["timeout"] == 3600
                    assert body["reasoning_effort"] == "high" and "temperature" not in body
                    option = m.base.live._option_ids_from_prompt(body["messages"][0]["content"])[0]
                    payload = {"model": "deepseek-v4-pro", "choices": [{"finish_reason": "stop",
                               "message": {"content": json.dumps({"expression": option}), "reasoning_content": "fake"}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
                    return type("Response", (), {"status": 200, "body": json.dumps(payload).encode()})()
            with patch.object(m.base, "UrllibHTTPTransport", Fake), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake"}):
                m.run(True)
                self.assertEqual(len(seen), 1 if fail else 48)
                with self.assertRaises(FileExistsError):
                    m.run(True)
            if fail:
                self.assertFalse((Path(d) / "generation.json").exists())
                self.assertEqual(m.base.read(Path(d) / "failure.json")["http_status"], 503)
            else:
                g = m.base.read(Path(d) / "generation.json")
                self.assertTrue(g["complete"])
                self.assertEqual([r["task_id"] for r in g["records"]], [t["task_id"] for t in p["tasks"]])
                self.assertEqual(g["provider_calls"], 48)
                self.assertFalse((Path(d) / "canary.json").exists())
                self.assertFalse((Path(d) / "analysis.json").exists())

    def test_exact48_no_canary_no_retry(self):
        self.exercise()

    def test_failure_stops_no_retry(self):
        self.exercise(fail=True)

    def test_explicit_execute_required(self):
        with self.assertRaises(ValueError):
            m.run(False)


if __name__ == "__main__":
    unittest.main()
