from collections import Counter
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import nonthinking_budget_live_20260910 as m
import nonthinking_budget_response_audit_20260910 as replay
from src.providers.openai_compatible import HTTPResponse, TransportError


class LiveTests(unittest.TestCase):
    def fixture(self):
        rows = m.transport._read_json(m.draft.OUT/'public-draft.json')['tasks']
        return m.public_schedule(rows)

    def test_body_only_cap_changes(self):
        tasks, bindings, caps = self.fixture()
        plan = {'settings':m.SETTINGS,'cap_by_task':caps}
        for a,b in zip(tasks[::2],tasks[1::2]):
            aa,bb = m.request_body(plan,a),m.request_body(plan,b)
            self.assertEqual({aa.pop('max_tokens'),bb.pop('max_tokens')},{256,8192})
            self.assertEqual(aa,bb)
            self.assertEqual(set(aa),set(m.SETTINGS)-{'max_tokens'}|{'messages'})
        self.assertEqual(len(set(t['task_id'] for t in tasks)),32)

    def test_adapter_restores(self):
        old = (m.transport.SETTINGS,m.transport.TIMEOUT_SECONDS,m.transport.request_body)
        with m.adapter():
            self.assertEqual(m.transport.TIMEOUT_SECONDS,120)
            self.assertEqual(m.transport.SETTINGS['thinking'],{'type':'disabled'})
        self.assertEqual(old,(m.transport.SETTINGS,m.transport.TIMEOUT_SECONDS,m.transport.request_body))

    def simulate(self, technical=False, invalid=False):
        tasks, bindings, caps = self.fixture()
        seen,lock = Counter(),threading.Lock()
        plan = {'settings':m.SETTINGS,'cap_by_task':caps,'endpoint':m.transport.ENDPOINT,
                'timeout_seconds':120,'interval_seconds':3,'formal_calls':32,'maximum_calls':34,'max_technical_retries':2}
        class Fake:
            def post(self,*,body,**kwargs):
                request = json.loads(body)
                key = (request['messages'][0]['content'],request['max_tokens'])
                with lock: seen[key]+=1; attempt=seen[key]
                if technical and attempt == 1: raise TransportError(category='timeout',delivery_ambiguous=True)
                option = m.transport.base.live._option_ids_from_prompt(key[0])[0]
                payload = {'model':'deepseek-v4-pro','choices':[{'finish_reason':'stop','message':{
                    'content':'bad-json' if invalid else json.dumps({'expression':option})}}],
                    'usage':{'prompt_tokens':650,'completion_tokens':10}}
                return HTTPResponse(status=200,body=json.dumps(payload).encode())
        with tempfile.TemporaryDirectory() as directory,m.adapter():
            out = Path(directory)
            pp = out/'plan.json'
            original = m.transport._read_json(m.draft.OLD/'plan.json')['construction_binding']
            pairs = m.transport._read_json(m.ROOT/original['private_key_relative_path'])['pairs']
            selected = m.transport._read_json(m.draft.OUT/'audit.json')['selected_pairs']
            by_id = {p['pair_id']:p for p in pairs}
            m.transport._write_exclusive_json(out/'private.json',{'pairs':[by_id[p['pair_id']] for p in selected],'bindings':bindings})
            plan['private_file_sha256'] = m.transport.sha(out/'private.json')
            plan['tasks'] = tasks
            plan['input_file_hashes'] = {str(p.relative_to(m.ROOT)):m.transport.sha(p)
                                        for p in (m.draft.OLD/'generation.json',m.THINK)}
            m.transport._write_exclusive_json(pp,plan)
            with mock.patch.object(m.transport,'OUT',out),mock.patch.object(m.transport,'PLAN_PATH',pp), \
                 mock.patch.object(m.transport,'_validate_plan',return_value=(plan,tasks)), \
                 mock.patch.object(m.transport.base,'UrllibHTTPTransport',Fake), \
                 mock.patch.object(m.transport.time,'sleep',return_value=None), \
                 mock.patch.dict(m.transport.os.environ,{'DEEPSEEK_API_KEY':'synthetic-only'}):
                gen = m.transport.run(True)
                with mock.patch.object(m,'OUT',out),mock.patch.object(m,'PLAN_PATH',pp), \
                     mock.patch.object(m,'validate',return_value=(plan,tasks)):
                    self.assertTrue(replay.audit()['verified'])
                    import nonthinking_budget_analysis_20260910 as analysis
                    result = analysis.analyze(pp,out/'private.json',out/'generation.json',out/'analysis.json',
                                              report_path=out/'results.md')
                    self.assertEqual(sum(c['missing'] for c in result['conditions'].values()),32-len(gen['records']))
                    for historical in result['historical_same_subset'].values():
                        self.assertEqual(sum(r['valid'] for r in historical['world_rows']),16)
                    for cap, condition in result['conditions'].items():
                        expected = 0
                        scores = {b['task_id']:b for b in bindings if b['max_tokens']==int(cap)}
                        labels = {p['arms'][a]['task_id']:p['arms'][a]['correct_option_ids'] for p in pairs for a in ('context_a','context_b')}
                        for r in gen['records']:
                            if r['task_id'] in scores and r['valid_choice']:
                                expected += r['selected_option_id'] in labels[scores[r['task_id']]['original_task_id']]
                        self.assertEqual(condition['own'],expected)
                    self.assertEqual(result,analysis.analyze(pp,out/'private.json',out/'generation.json',out/'analysis.json',
                                                            report_path=out/'results.md',write=False))
                with self.assertRaises(FileExistsError): m.transport.run(True)
                self.assertEqual(sum(seen.values()),gen['provider_calls'])
                return gen

    def test_normal(self):
        g=self.simulate()
        self.assertEqual((g['provider_calls'],g['valid_response_count']),(32,32))

    def test_technical_max34(self):
        g=self.simulate(technical=True)
        self.assertEqual(g['provider_calls'],34)
        self.assertEqual([s['attempt_count'] for s in g['slots']],[2,2]+[1]*30)

    def test_invalid_not_retried(self):
        g=self.simulate(invalid=True)
        self.assertEqual((g['provider_calls'],g['valid_response_count']),(32,0))


if __name__ == '__main__': unittest.main()
