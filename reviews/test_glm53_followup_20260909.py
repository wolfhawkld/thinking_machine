import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("glm", Path(__file__).with_name("glm53-followup-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class GlmTests(unittest.TestCase):
    def exercise(self, mode="ok"):
        with tempfile.TemporaryDirectory() as d, patch.object(m, "OUT", Path(d)), contextlib.redirect_stdout(io.StringIO()):
            m.plan(); p = m.checked_plan()
            seen = []; lock = threading.Lock()
            class Fake:
                def post(self, **kw):
                    b = json.loads(kw["body"])
                    assert kw["url"] == m.ENDPOINT and kw["timeout"] == 3600
                    assert b["model"] == "glm-5.3" and b["max_tokens"] == 131072
                    assert b["reasoning_effort"] == "high" and len(b["messages"]) == 1
                    with lock: seen.append(b)
                    if mode == "http": raise m.base.HTTPStatusError(429)
                    option = m.base.live._option_ids_from_prompt(b["messages"][0]["content"])[0]
                    payload = {"model": "wrong" if mode == "model" else "glm-5.3",
                               "choices": [{"finish_reason": "stop", "message": {
                               "content": "bad" if mode == "invalid" else json.dumps({"expression": option}),
                               "reasoning_content": "synthetic"}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
                    return type("Response", (), {"status": 200, "body": json.dumps(payload).encode()})()
            with patch.object(m.base, "UrllibHTTPTransport", Fake), patch.object(m, "read_key", return_value="fake-key"), patch.object(m.time, "sleep"):
                m.run(True)
                count = len(seen)
                with self.assertRaises(FileExistsError): m.run(True)
                self.assertEqual(len(seen), count)
            if mode == "ok":
                self.assertEqual(count, 50)
                g = m.base.read(Path(d) / "generation.json")
                self.assertTrue(g["complete"])
                self.assertEqual([r["task_id"] for r in g["records"]], [t["task_id"] for t in p["tasks"]])
            else:
                self.assertLessEqual(count, 2)
                self.assertFalse(m.base.read(Path(d) / "canary.json")["passed"])
                self.assertFalse((Path(d) / "generation.json").exists())
            for f in Path(d).glob("*.json*"):
                self.assertNotIn("fake-key", f.read_text())

    def test_2plus48_original_tasks_no_secret(self): self.exercise()
    def test_canary_format_failure_blocks_benchmark(self): self.exercise("invalid")
    def test_wrong_model_blocks_benchmark(self): self.exercise("model")
    def test_http_failure_no_retry(self): self.exercise("http")


if __name__ == "__main__":
    unittest.main()
