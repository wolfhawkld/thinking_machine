import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("stream", Path(__file__).with_name("glm53-stream-canary-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.task = m.glm.checked_plan()["canary_tasks"][0]
        self.option = m.base.live._option_ids_from_prompt(self.task["rendered_prompt"])[0]

    def lines(self):
        return [json.dumps({"model": "glm-5.3", "choices": [{"index": 0, "delta": {"reasoning_content": "推理"}}]}),
                json.dumps({"model": "glm-5.3", "choices": [{"index": 0, "delta": {"content": json.dumps({"expression": self.option})}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}), "[DONE]"]

    def test_parser_reasoning_and_usage(self):
        state = m.StreamState()
        for line in self.lines(): state.feed("data: " + line)
        r = state.record(self.task, 1)
        self.assertTrue(r["valid_choice"])
        self.assertEqual(r["thinking_telemetry"]["reasoning_character_count"], 2)
        self.assertNotIn("推理", json.dumps(r, ensure_ascii=False))

    def test_incomplete_stream_rejected(self):
        state = m.StreamState()
        for line in self.lines()[:-1]: state.feed("data: " + line)
        with self.assertRaises(ValueError): state.record(self.task, 1)

    def test_wrong_model_rejected(self):
        with self.assertRaises(ValueError): m.StreamState().feed('data: {"model":"other"}')

    def test_local_fake_curl_success(self):
        output = 'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n' + ''.join('data: ' + x + '\n\n' for x in self.lines())
        original = subprocess.Popen
        def fake(args, **kwargs):
            self.assertIn('--max-time', args)
            self.assertNotIn('fake-key', ' '.join(args))
            return original([sys.executable, '-c', 'import sys;sys.stdin.buffer.read();sys.stdout.write(' + repr(output) + ');sys.stdout.flush()'], **kwargs)
        with patch.object(m.subprocess, 'Popen', fake):
            result = m.request(self.task, 'fake-key', lambda event: None)
        self.assertIsNone(result['failure'])
        self.assertEqual(result['telemetry']['curl_exit'], 0)
        self.assertTrue(result['record']['valid_choice'])

    def test_no_progress_terminates_child(self):
        original = subprocess.Popen
        def fake(args, **kwargs):
            return original([sys.executable, '-c', 'import sys,time;sys.stdin.buffer.read();time.sleep(30)'], **kwargs)
        with patch.object(m.subprocess, 'Popen', fake), patch.object(m, 'NO_PROGRESS_TIMEOUT', 0.01):
            result = m.request(self.task, 'fake-key', lambda event: None)
        self.assertEqual(result['failure'], 'no_reasoning_or_answer_progress_120s')
        self.assertIsNone(result['record'])


if __name__ == '__main__': unittest.main()
