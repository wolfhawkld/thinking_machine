import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("retry", Path(__file__).with_name("glm53-retry-merge-20260909.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.old, self.g = m.original()
        task = self.old['tasks'][24]
        option = m.base.live._option_ids_from_prompt(task['rendered_prompt'])[0]
        payload = {'model':'glm-5.3','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'expression':option}),'reasoning_content':'fake'}}], 'usage':{'prompt_tokens':1,'completion_tokens':2}}
        self.retry = {'record':m.live.stream.glm.response_record(task,payload,1),'failure':None,'telemetry':{}}

    def test_merge_only_missing_slot(self):
        previous = copy.deepcopy(self.g)
        _, records = m.merge(self.old,self.g,self.retry)
        self.assertEqual(self.g,previous)
        self.assertEqual(len(records),48)
        for i,r in enumerate(records):
            self.assertEqual(r,self.retry['record'] if i==24 else self.g['results'][i]['record'])

    def test_failed_or_wrong_task_cannot_merge(self):
        with self.assertRaises(ValueError):m.merge(self.old,self.g,{'failure':'timeout','record':None})
        r=copy.deepcopy(self.retry);r['record']['task_id']='wrong'
        with self.assertRaises(ValueError):m.merge(self.old,self.g,r)

    def test_exact_one_call_and_no_restart(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'OUT',Path(d)),contextlib.redirect_stdout(io.StringIO()):
            m.plan()
            with patch.object(m.live.stream,'request',return_value=self.retry) as request,patch.object(m.live.stream.glm,'read_key',return_value='fake-key'):
                m.run(True)
                self.assertEqual(request.call_count,1)
                self.assertEqual(request.call_args.args[0]['task_id'],self.old['tasks'][24]['task_id'])
                with self.assertRaises(FileExistsError):m.run(True)
                self.assertEqual(request.call_count,1)
            g=m.base.read(Path(d)/'generation.json')
            self.assertEqual(g['known_task_physical_attempts'],49)
            self.assertFalse(g['usage_complete'])
            for f in Path(d).glob('*.json*'):self.assertNotIn('fake-key',f.read_text())


if __name__ == '__main__':unittest.main()
