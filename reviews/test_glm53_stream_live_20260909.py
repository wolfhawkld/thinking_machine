import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("glm53-stream-live-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LiveTests(unittest.TestCase):
    def exercise(self, failure=False):
        with tempfile.TemporaryDirectory() as folder, patch.object(m, 'OUT', Path(folder)), contextlib.redirect_stdout(io.StringIO()):
            m.plan(); p = m.checked_plan()
            def fake(task, key, notify):
                if failure: return {'record': None, 'failure': 'timeout', 'telemetry': {}}
                option = m.base.live._option_ids_from_prompt(task['rendered_prompt'])[0]
                payload = {'model': 'glm-5.3', 'choices': [{'finish_reason': 'stop', 'message': {
                    'content': json.dumps({'expression': option}), 'reasoning_content': 'fake'}}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}
                return {'record': m.stream.glm.response_record(task, payload, 1), 'failure': None, 'telemetry': {}}
            with patch.object(m.stream, 'request', side_effect=fake) as request, patch.object(m.stream.glm, 'read_key', return_value='fake-key'), patch.object(m.time, 'sleep'):
                m.run(True)
                calls = request.call_count
                with self.assertRaises(FileExistsError): m.run(True)
                self.assertEqual(request.call_count, calls)
            if failure:
                self.assertFalse((Path(folder)/'generation.json').exists())
                self.assertFalse(m.base.read(Path(folder)/'failure.json')['complete'])
            else:
                self.assertEqual(calls, 48)
                g = m.base.read(Path(folder)/'generation.json')
                self.assertTrue(g['complete'])
                self.assertEqual([r['task_id'] for r in g['records']], [t['task_id'] for t in p['tasks']])
                self.assertFalse((Path(folder)/'canary.json').exists())
            for f in Path(folder).glob('*.json*'): self.assertNotIn('fake-key', f.read_text())

    def test_exact48_no_canary_or_retry(self): self.exercise()
    def test_failure_drains_and_records(self): self.exercise(True)


if __name__ == '__main__': unittest.main()
