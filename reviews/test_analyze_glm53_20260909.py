import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("analysis", Path(__file__).with_name("analyze-glm53-20260909.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AnalysisTests(unittest.TestCase):
    def test_complete_validation(self):
        _,_,g=a.validated_generation()
        self.assertEqual(len(g['records']),48)

    def test_changed_saved_record_rejected(self):
        original=a.base.read
        def changed(path):
            d=original(path)
            if Path(path)==a.m.OUT/'generation.json':
                d=copy.deepcopy(d);d['records'][0]['selected_option_id']='other'
            return d
        with patch.object(a.base,'read',side_effect=changed),self.assertRaises(ValueError):a.validated_generation()

    def test_usage_cannot_hide_old_failure(self):
        original=a.base.read
        def changed(path):
            d=original(path)
            if Path(path)==a.m.OUT/'generation.json':d['usage_complete']=True
            return d
        with patch.object(a.base,'read',side_effect=changed),self.assertRaises(ValueError):a.validated_generation()

    def test_direct_arithmetic(self):
        _,old,g=a.validated_generation()
        private=a.base.read(a.base.ROOT/old['benchmark_binding']['private_key_relative_path'])
        choices={r['task_id']:r['selected_option_id'] for r in g['records']}
        own=cross=complete=positive=negative=tie=0
        for pair in private['pairs']:
            aa,bb=(pair['arms'][arm] for arm in ('context_a','context_b'))
            x,y=choices[aa['task_id']],choices[bb['task_id']]
            ca,cb=aa['correct_option_ids'][0],bb['correct_option_ids'][0]
            o,c=int(x==ca)+int(y==cb),int(x==cb)+int(y==ca)
            own+=o;cross+=c;complete+=o==2;positive+=o>c;negative+=o<c;tie+=o==c
        self.assertEqual((own,cross,complete,positive,negative,tie),(38,1,15,22,0,2))

    def test_exact_replay(self):
        expected=a.base.read(a.m.OUT/'analysis.json')
        with patch.object(a.base,'save') as save,contextlib.redirect_stdout(io.StringIO()):a.analyze()
        self.assertEqual(save.call_args.args[1],expected)


if __name__=='__main__':unittest.main()
