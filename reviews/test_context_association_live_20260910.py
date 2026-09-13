import copy
from collections import Counter
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from src.providers.openai_compatible import HTTPResponse, TransportError

import context_association_live_20260910 as m
import context_association_response_audit_20260910 as response_audit


class LiveTests(unittest.TestCase):
    def fixture(self):
        tasks, bindings = [], []
        for i in range(18):
            for condition in m.materials.CONDITIONS:
                task_id = f'{i}-{condition}'
                tasks.append({'task_id': task_id, 'rendered_prompt': 'public', 'prompt_sha256': 'h'})
                bindings.append({'task_id': task_id, 'original_task_id': str(i), 'condition': condition})
        return {'tasks': tasks}, {'bindings': bindings}

    def test_balanced_schedule_is_bijective_and_input_order_independent(self):
        public, private = self.fixture()
        scheduled = m.scheduled_tasks(public, private)
        self.assertEqual(len(scheduled), 54)
        cond = {r['task_id']: r['condition'] for r in private['bindings']}
        for position in range(3):
            self.assertEqual(Counter(cond[scheduled[i]['task_id']] for i in range(position, 54, 3)),
                             {c: 6 for c in m.materials.CONDITIONS})
        public['tasks'].reverse()
        private['bindings'].reverse()
        self.assertEqual(scheduled, m.scheduled_tasks(public, private))

    def test_duplicate_missing_conditions_rejected(self):
        public, private = self.fixture()
        private['bindings'].pop()
        with self.assertRaises(ValueError): m.scheduled_tasks(public, private)
        public, private = self.fixture()
        private['bindings'].append(copy.deepcopy(private['bindings'][0]))
        with self.assertRaises(ValueError): m.scheduled_tasks(public, private)

    def test_adapter_restores_on_exception_and_exact_budget(self):
        previous = (m.transport.OUT, m.transport.HERE, m.transport.TASK_COUNT, m.transport.MAX_CALLS)
        with self.assertRaises(RuntimeError):
            with m.adapter():
                self.assertEqual((m.transport.TASK_COUNT, m.transport.MAX_CALLS, m.transport.MAX_TECHNICAL_RETRIES), (54, 56, 2))
                self.assertEqual(m.transport.OUT, m.OUT)
                raise RuntimeError('synthetic')
        self.assertEqual(previous, (m.transport.OUT, m.transport.HERE, m.transport.TASK_COUNT, m.transport.MAX_CALLS))

    def test_real_draft_parser_and_54_plus_two_synthetic_budget(self):
        public = m.transport._read_json(m.materials.OUT / 'public-draft.json')
        tasks = public['tasks']
        seen, lock = Counter(), threading.Lock()
        class FakeTransport:
            def post(self, *, body, **kwargs):
                prompt = json.loads(body)['messages'][0]['content']
                with lock:
                    seen[prompt] += 1
                    attempt = seen[prompt]
                if attempt == 1:
                    raise TransportError(category='timeout', delivery_ambiguous=True)
                option = m.transport.base.live._option_ids_from_prompt(prompt)[0]
                return HTTPResponse(status=200, body=json.dumps({'model': 'deepseek-v4-pro',
                    'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'expression': option})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}).encode())
        for task in tasks:
            self.assertEqual(len(m.transport.base.live._option_ids_from_prompt(task['rendered_prompt'])), 10)
        plan = {'settings': m.SETTINGS, 'endpoint': m.transport.ENDPOINT, 'timeout_seconds': 3600, 'interval_seconds': 3}
        with tempfile.TemporaryDirectory() as directory, m.adapter():
            path = Path(directory)
            plan_path = path / 'plan.json'
            m.transport._write_exclusive_json(plan_path, plan)
            with mock.patch.object(m.transport, 'OUT', path), mock.patch.object(m.transport, 'PLAN_PATH', plan_path), \
                 mock.patch.object(m.transport, '_validate_plan', return_value=(plan, tasks)), \
                 mock.patch.object(m.transport.base, 'UrllibHTTPTransport', FakeTransport), \
                 mock.patch.object(m.transport.time, 'sleep', return_value=None), \
                 mock.patch.dict(m.transport.os.environ, {'DEEPSEEK_API_KEY': 'synthetic-only'}):
                result = m.transport.run(True)
                self.assertEqual((result['provider_calls'], len(result['slots']), len(result['records'])), (56, 54, 2))
                self.assertEqual(sum(seen.values()), 56)
                self.assertEqual([s['attempt_count'] for s in result['slots']], [2, 2] + [1] * 52)
                with mock.patch.object(m, 'OUT', path), mock.patch.object(m, 'PLAN_PATH', plan_path), \
                     mock.patch.object(m, 'validate', return_value=(plan, tasks)):
                    replay = response_audit.audit()
                    self.assertTrue(replay['verified'])
                    self.assertEqual((replay['physical_attempts'], replay['raw_response_replays'], replay['technical_retries']), (56, 2, 2))
                with self.assertRaises(FileExistsError): m.transport.run(True)
                self.assertEqual(sum(seen.values()), 56)


if __name__ == '__main__': unittest.main()
