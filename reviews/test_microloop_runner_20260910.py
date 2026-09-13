import json
from pathlib import Path
import tempfile
import unittest

from microloop_runner_20260910 import run_trajectory, run_collection, TechnicalFailure, load_materials, ROOT


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pub, cls.priv = load_materials(ROOT / 'artifacts/microloop-draft-20260910')

    def test_resume_no_resend(self):
        pub = self.pub[0]
        labels = {tuple(r['point']): r['label'] for r in self.priv[0]['evidence']}
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def send(prompt):
                calls.append(prompt)
                return {'content': '{}'}
            row = run_trajectory(pub, 'random', lambda q: labels[tuple(q)], send, Path(tmp), {'remaining': 2})
            replay = run_trajectory(pub, 'random', lambda q: labels[tuple(q)], send, Path(tmp), {'remaining': 2})
            self.assertEqual(row, replay)
            self.assertEqual(len(calls), 3)  # malformed responses never retried

    def test_retry_limit_and_terminal_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def fail(prompt):
                calls.append(prompt)
                raise TechnicalFailure('must not be logged')
            budget = {'remaining': 2}
            row = run_trajectory(self.pub[0], 'active', lambda q: self.fail(), fail, Path(tmp), budget)
            self.assertEqual(len(calls), 5)
            self.assertEqual(row['responses'], [None] * 3)
            run_trajectory(self.pub[0], 'active', lambda q: self.fail(), fail, Path(tmp), {'remaining': 2})
            self.assertEqual(len(calls), 5)
            self.assertNotIn('must not be logged', ''.join(p.read_text() for p in Path(tmp).glob('*.json')))

    def test_unknown_attempt_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            def crash(prompt):
                raise RuntimeError('interruption')
            with self.assertRaises(RuntimeError):
                run_trajectory(self.pub[0], 'active', None, crash, Path(tmp), {'remaining': 0})
            with self.assertRaisesRegex(RuntimeError, 'Unresolved'):
                run_trajectory(self.pub[0], 'active', None, lambda p: self.fail(), Path(tmp), {'remaining': 0})

    def test_feedback_in_next_prompt(self):
        pub = self.pub[0]
        labels = {tuple(r['point']): r['label'] for r in self.priv[0]['evidence']}
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def send(prompt):
                calls.append(prompt)
                data = {'expression': pub['parent']}
                if len(calls) < 3:
                    data['query'] = pub['query_points'][len(calls) - 1]
                return {'content': json.dumps(data)}
            run_trajectory(pub, 'active', lambda q: labels[tuple(q)], send, Path(tmp), {'remaining': 0})
            feedback = {'point': pub['query_points'][0], 'label': labels[tuple(pub['query_points'][0])]}
            self.assertIn(json.dumps(feedback), calls[1])
            self.assertIn('Final response; no further queries.', calls[2])

    def test_parallel_global_budget_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            def factory(pub):
                def fail(prompt): raise TechnicalFailure()
                return fail
            path = Path(tmp)
            rows = run_collection(self.pub, self.priv, factory, path, max_retries=2, workers=4)
            self.assertEqual(len(rows), 36)
            self.assertEqual(len(list(path.glob('*/*.request.json'))), 110)
            self.assertEqual(rows, run_collection(self.pub, self.priv, factory, path, max_retries=2, workers=4))
            self.assertEqual(len(list(path.glob('*/*.request.json'))), 110)

    def test_exclusive_orchestrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'orchestrator.lock').touch()
            with self.assertRaises(FileExistsError):
                run_collection(self.pub, self.priv, None, path)

    def test_fatal_error_stops_queued_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            def factory(pub):
                def crash(prompt): raise ValueError('fatal configuration error')
                return crash
            path = Path(tmp)
            with self.assertRaises((ValueError, RuntimeError)):
                run_collection(self.pub, self.priv, factory, path, workers=4)
            self.assertLessEqual(len(list(path.glob('*/*.request.json'))), 4)


if __name__ == '__main__': unittest.main()
