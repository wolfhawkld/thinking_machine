import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("staggered", Path(__file__).with_name("deepseek-thinking-128k-staggered-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as folder, patch.object(m, "OUT", Path(folder)), contextlib.redirect_stdout(io.StringIO()):
            m.plan()
            p, old, preserved = m.checked_plan()
            seen = []
            mutex = threading.Lock()
            class Fake:
                def post(self, **kw):
                    body = json.loads(kw["body"])
                    with mutex:
                        seen.append(body)
                    assert body["max_tokens"] == 131072 and kw["timeout"] == 3600
                    if fail:
                        raise m.base.HTTPStatusError(503)
                    option = m.base.live._option_ids_from_prompt(body["messages"][0]["content"])[0]
                    payload = {"model": "deepseek-v4-pro", "choices": [{"finish_reason": "stop",
                               "message": {"content": json.dumps({"expression": option}), "reasoning_content": "fake"}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
                    return type("Response", (), {"status": 200, "body": json.dumps(payload).encode()})()
            with patch.object(m.base, "UrllibHTTPTransport", Fake), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake"}), patch.object(m.time, "sleep") as sleep:
                m.run(True)
                count = len(seen)
                with self.assertRaises(FileExistsError):
                    m.run(True)
                self.assertEqual(len(seen), count)
                self.assertTrue(all(c.args == (3,) for c in sleep.call_args_list))
            if fail:
                result = m.base.read(Path(folder) / "failure.json")
                self.assertFalse(result["complete"])
                self.assertTrue(result["failures"])
                self.assertFalse((Path(folder) / "generation.json").exists())
            else:
                result = m.base.read(Path(folder) / "generation.json")
                self.assertEqual(count, len(p["pending_indices"]))
                self.assertEqual(len(result["records"]), 48)
                self.assertEqual([r["task_id"] for r in result["records"]], [t["task_id"] for t in old["tasks"]])
                for index, record in preserved.items():
                    self.assertEqual(result["records"][index], record)
                self.assertEqual(result["known_physical_attempts"], 49)
                self.assertFalse(result["total_usage_complete"])
                prompts = {b["messages"][0]["content"] for b in seen}
                self.assertEqual(prompts, {old["tasks"][i]["rendered_prompt"] for i in p["pending_indices"]})

    def test_preserve_and_merge_no_duplicate(self):
        self.exercise()

    def test_error_stops_dispatch_and_drains(self):
        self.exercise(fail=True)


if __name__ == "__main__":
    unittest.main()
