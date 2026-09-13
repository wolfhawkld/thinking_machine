import json
import unittest
from pathlib import Path
import microloop_revision_20260910 as revision
from src import dsl


class RevisionTests(unittest.TestCase):
    def test_predicate_equivalence(self):
        for op in ('gt','eq'):
            p = f'({op} (var x1) (const 0))'
            wrapped = '(ite '+p+' (const 1) (const 0))'
            self.assertEqual(revision.normalize_expression(p),revision.normalize_expression(wrapped))

    def test_whitespace(self):
        self.assertEqual(revision.normalize_expression(' ( eq\n(var x1) (const 0)) '),
                         revision.normalize_expression('(eq (var x1) (const 0))'))

    def test_reject_bad_grammar(self):
        for e in ('(gt (var x1))','(gt (var x1) (const 0)) extra',
                  '(add (eq (var x1) (const 0)) (const 1))',
                  '(ite (const 1) (const 0) (const 1))','(const 9)','(var x1)'):
            with self.subTest(e=e), self.assertRaises(ValueError): revision.normalize_expression(e)

    def test_depth_after_expansion(self):
        e='(var x1)'
        for _ in range(3): e='(neg '+e+')'
        with self.assertRaises(ValueError): revision.normalize_expression('(gt '+e+' (const 0))')

    def test_schema_not_relaxed(self):
        self.assertFalse(revision.parse_response('{"expression":"(eq (var x1) (const 0))","extra":1}',True)['valid_program'])
        self.assertFalse(revision.parse_response(None,True)['valid_program'])

    def test_node_limit_preserved(self):
        p='(gt (var x1) (const 0))'
        e='(var x2)'
        for _ in range(3): e=f'(ite {p} {e} {e})'
        with self.assertRaisesRegex(ValueError,'node count'):
            revision.normalize_expression(e)

    def test_multiround_original_feedback_and_history(self):
        root=Path(__file__).resolve().parents[1]/'artifacts/microloop-draft-20260910'
        p=json.loads((root/'public.json').read_text())['worlds'][0]
        old=revision.original.initial_state(p,'active'); new=revision.initial_state(p,'active')
        for i in range(3):
            data={'expression':'(eq (var x1) (const 0))'}
            if i<2: data['query']=p['query_points'][i]
            content=json.dumps(data)
            old=revision.original.advance(p,old,content,lambda q:0)
            new=revision.advance(p,new,content,lambda q:0)
            self.assertEqual(old['observations'],new['observations'])
            self.assertEqual(new['history'][-1]['response'],content)
            self.assertTrue(new['history'][-1]['valid_program'])
            if i<2:
                self.assertEqual(revision.original.render_prompt(p,old),revision.original.render_prompt(p,new))
                self.assertIn('AFTER root-predicate expansion',revision.render_prompt(p,new))


if __name__=='__main__': unittest.main()
