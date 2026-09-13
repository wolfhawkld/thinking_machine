from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import new_evidence_live_20260910 as m
from src.providers.openai_compatible import HTTPResponse, TransportError


def payload(content='{"expression":"(const 0)"}', finish='stop'):
    return {'model': m.SETTINGS['model'], 'choices': [{'finish_reason': finish,
            'message': {'content': content, 'reasoning_content': 'synthetic reasoning'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}


class LiveTests(unittest.TestCase):
    def fixture(self):
        public = m.transport._read_json(m.materials.OUT / 'public-draft.json')
        private = m.transport._read_json(m.materials.OUT / 'private-draft.json')
        return public, private

    def test_balanced_schedule_and_input_order_independence(self):
        public, private = self.fixture()
        scheduled = m.scheduled_tasks(public, private)
        conditions = {b['task_id']: b['condition'] for b in private['bindings']}
        for position in range(3):
            self.assertEqual(Counter(conditions[scheduled[i]['task_id']] for i in range(position, 27, 3)),
                             {c: 3 for c in m.scoring.CONDITIONS})
        public['tasks'].reverse()
        private['bindings'].reverse()
        self.assertEqual(scheduled, m.scheduled_tasks(public, private))
        private['bindings'].append(copy.deepcopy(private['bindings'][0]))
        with self.assertRaises(ValueError): m.scheduled_tasks(public, private)

    def test_parser_matches_frozen_scorer_without_labels(self):
        public, private = self.fixture()
        task, binding = public['tasks'][0], private['bindings'][0]
        for content in ('{"expression":"(const 0)"}', '{"expression":"(var x1)"}',
                        'not-json', None, [], '{"expression":"Q12345678"}',
                        '{"expression":"(const 0)","extra":1}'):
            with self.subTest(content=content):
                r = m.record_response(task, payload(content), 12)
                self.assertEqual(r['content'], content)
                self.assertEqual(r['valid_choice'], m.scoring.score_expression(content, binding)['valid_program'])
                self.assertTrue(r['thinking_telemetry']['reasoning_content_present'])
        self.assertTrue(m.record_response(task, payload(finish='length'), 1)['thinking_telemetry']['output_truncated'])
        bad = payload()
        bad['model'] = 'other-model'
        with self.assertRaises(ValueError): m.record_response(task, bad, 1)

    def test_adapter_restores_even_after_exception(self):
        old = m.transport.base.record_response
        count = m.transport.TASK_COUNT
        with self.assertRaises(RuntimeError):
            with m.adapter():
                self.assertIs(m.transport.base.record_response, m.record_response)
                self.assertEqual((m.transport.TASK_COUNT, m.transport.MAX_CALLS), (27, 29))
                raise RuntimeError('synthetic')
        self.assertIs(m.transport.base.record_response, old)
        self.assertEqual(m.transport.TASK_COUNT, count)

    def simulate(self, mode):
        public, private = self.fixture()
        tasks = m.scheduled_tasks(public, private)
        seen, lock = Counter(), threading.Lock()
        class FakeTransport:
            def post(self, *, body, **kwargs):
                request = json.loads(body)
                self_test.assertEqual(set(request), set(m.SETTINGS) | {'messages'})
                self_test.assertEqual(set(request['messages'][0]), {'role', 'content'})
                prompt = request['messages'][0]['content']
                with lock:
                    seen[prompt] += 1
                    attempt = seen[prompt]
                if mode == 'technical' and attempt == 1:
                    raise TransportError(category='timeout', delivery_ambiguous=True)
                if mode == 'auth': return HTTPResponse(status=401, body=b'{"error":"synthetic"}')
                content = 'not-json' if mode == 'invalid' else '{"expression":"(const 0)"}'
                return HTTPResponse(status=200, body=json.dumps(payload(content)).encode())
        self_test = self
        plan = {'settings': m.SETTINGS, 'endpoint': m.transport.ENDPOINT, 'timeout_seconds': 3600, 'interval_seconds': 3,
                'formal_calls': 27, 'maximum_calls': 29, 'max_technical_retries': 2}
        with tempfile.TemporaryDirectory() as directory, m.adapter():
            path = Path(directory)
            plan_path = path / 'plan.json'
            private_path = path / 'private.json'
            m.transport._write_exclusive_json(private_path, private)
            plan['private_file_sha256'] = m.transport.sha(private_path)
            plan['source_private_file_sha256'] = plan['private_file_sha256']
            baseline_path = m.materials.OUT / 'code-baselines.json'
            plan['input_file_hashes'] = {str(baseline_path.relative_to(m.ROOT)): m.transport.sha(baseline_path)}
            m.transport._write_exclusive_json(plan_path, plan)
            with mock.patch.object(m.transport, 'OUT', path), mock.patch.object(m.transport, 'PLAN_PATH', plan_path), \
                 mock.patch.object(m.transport, '_validate_plan', return_value=(plan, tasks)), \
                 mock.patch.object(m.transport.base, 'UrllibHTTPTransport', FakeTransport), \
                 mock.patch.object(m.transport.time, 'sleep', return_value=None), \
                 mock.patch.dict(m.transport.os.environ, {'DEEPSEEK_API_KEY': 'synthetic-only'}):
                result = m.transport.run(True)
                self.assertEqual(len(result['slots']), 27)
                self.assertEqual(sum(seen.values()), result['provider_calls'])
                events = [json.loads(line) for line in (path / 'attempts.jsonl').read_text().splitlines()]
                self.assertEqual(events[-1]['event'], 'complete')
                self.assertEqual(len(list(path.glob('attempt-*.json'))), result['provider_calls'])
                import new_evidence_response_audit_20260910 as replay
                with mock.patch.object(m, 'OUT', path), mock.patch.object(m, 'PLAN_PATH', plan_path), \
                     mock.patch.object(m, 'validate', return_value=(plan, tasks)):
                    audit = replay.audit()
                    self.assertTrue(audit['verified'])
                    self.assertEqual(audit['physical_attempts'], result['provider_calls'])
                    import new_evidence_live_analysis_20260910 as analysis
                    scored = analysis.analyze(plan_path, private_path, path / 'generation.json',
                                              path / 'analysis.json', baseline_path=baseline_path,
                                              report_path=path / 'results.md', write=True)
                    self.assertEqual(scored['tasks'], 27)
                    self.assertEqual(scored, analysis.analyze(plan_path, private_path, path / 'generation.json',
                                                             path / 'analysis.json', baseline_path=baseline_path,
                                                             report_path=path / 'results.md', write=False))
                    self.assertTrue((path / 'results.md').is_file())
                with self.assertRaises(FileExistsError): m.transport.run(True)
                self.assertEqual(sum(seen.values()), result['provider_calls'])
                return result

    def test_27_plus2_bound_and_lowest_index_retry(self):
        result = self.simulate('technical')
        self.assertEqual(result['provider_calls'], 29)
        self.assertEqual([s['attempt_count'] for s in result['slots']], [2, 2] + [1] * 25)
        self.assertEqual(result['valid_response_count'], 2)

    def test_invalid_content_not_retried(self):
        result = self.simulate('invalid')
        self.assertEqual(result['provider_calls'], 27)
        self.assertEqual(result['valid_response_count'], 0)
        self.assertEqual(len(result['records']), 27)

    def test_normal27_valid_responses_complete_pipeline(self):
        result = self.simulate('normal')
        self.assertEqual(result['provider_calls'], 27)
        self.assertEqual(result['valid_response_count'], 27)
        self.assertTrue(result['usage_complete'])

    def test_auth_error_stops_and_never_retries(self):
        result = self.simulate('auth')
        self.assertLessEqual(result['provider_calls'], 27)
        self.assertTrue(all(s['attempt_count'] <= 1 for s in result['slots']))
        self.assertEqual(result['valid_response_count'], 0)


if __name__ == '__main__': unittest.main()
