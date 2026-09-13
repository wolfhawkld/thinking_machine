import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('runner', Path(__file__).with_name('shortcut_challenge_runner_20260909.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BudgetTests(unittest.TestCase):
    def test_calibration_in_total(self):
        self.assertEqual(m.remaining(590, 3), 10)
        self.assertEqual(m.remaining(590, 4), 3010)

    def test_partial_not_negative(self):
        self.assertEqual(m.deadline_status(2), 'calibration_budget_exhausted')
        self.assertEqual(m.deadline_status(8), 'scan_budget_exhausted')

    def test_resume_does_not_reset(self):
        state = {'status': 'paused_at_boundary', 'active_index': None, 'elapsed_seconds': 3601, 'completed': [0]*4}
        with self.assertRaises(ValueError): m.resumable(state)

    def test_no_interrupted_redraw(self):
        state = {'status': 'interrupted_incomplete_world', 'active_index': 0}
        with self.assertRaises(ValueError): m.resumable(state)

    def test_crashed_state_not_restartable(self):
        with self.assertRaises(ValueError): m.resumable({'status': 'running'})

    def test_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'world.json'
            m.save(path, {'ok': True}, exclusive=True)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): m.save(path, {}, exclusive=True)

    def test_deadline_kills_worker_without_next_candidate(self):
        clock = [0.0]
        class FakeProcess:
            alive = False
            exitcode = None
            started = 0
            def __init__(self, **kwargs): pass
            def start(self):
                self.alive = True
                type(self).started += 1
            def is_alive(self): return self.alive
            def join(self, timeout=None):
                if self.alive: clock[0] = 600.01
            def terminate(self):
                self.alive = False
                self.exitcode = -15
            def kill(self): self.terminate()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(m, 'OUT', Path(tmp)/'run'), \
             patch.object(m, 'manifest', return_value={'seeds': list(range(128))}), \
             patch.object(m.subprocess, 'run'), \
             patch.object(m.mp, 'get_context', return_value=SimpleNamespace(Process=FakeProcess)), \
             patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]):
            state = m.run()
        self.assertEqual(state['status'], 'calibration_budget_exhausted')
        self.assertEqual(state['completed'], [])
        self.assertEqual(state['incomplete_index'], 0)
        self.assertEqual(FakeProcess.started, 1)


if __name__ == '__main__':
    unittest.main()
