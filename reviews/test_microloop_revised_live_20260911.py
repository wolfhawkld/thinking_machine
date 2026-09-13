import json
from pathlib import Path
import tempfile
import unittest
import microloop_revised_live_20260911 as revised


class RevisedLiveTests(unittest.TestCase):
    def test_isolated_adapter(self):
        live=revised.adapter()
        self.assertEqual(live.OUT,revised.OUT)
        self.assertIs(live.aggregate,revised.revision.aggregate)
        self.assertIsNot(live.runner,revised.original_live.runner)
        self.assertIs(revised.original_live.runner.protocol,revised.revision.original)

    def test_full_mock_chain_uses_new_prompt_and_accepts_predicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            live=revised.adapter(Path(tmp))
            public,private=live.runner.load_materials(live.DRAFT)
            def factory(pub):
                def send(prompt):
                    self.assertIn('Expression grammar:',prompt)
                    self.assertIn('AFTER root-predicate expansion',prompt)
                    final='Final response; no further queries.' in prompt
                    data={'expression':'(eq (var x1) (const 0))'}
                    if not final:
                        stage=0 if 'Measurement stage 1.' in prompt else 1
                        data['query']=pub['query_points'][stage]
                    return {'content':json.dumps(data),'usage':{'prompt_tokens':1,'completion_tokens':1}}
                return send
            rows=live.runner.run_collection(public,private,factory,live.OUT,max_retries=2,workers=4)
            live.finish(public,private,rows)
            analysis=json.loads((live.OUT/'analysis.json').read_text())
            self.assertTrue(all(c['stage_metrics'][2]['valid']==12 for c in analysis['conditions'].values()))
            self.assertEqual(json.loads((live.OUT/'complete.json').read_text())['physical_attempts'],108)


if __name__=='__main__': unittest.main()
