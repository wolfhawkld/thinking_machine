import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import microloop_materials_20260910 as m


class MaterialTests(unittest.TestCase):
    def test_fixed_unique_seed_vector(self):
        self.assertEqual(m.seeds(), m.seeds())
        self.assertEqual(len(set(m.seeds())), 12)

    def test_exclusive_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "file.json"
            m.save(p, {"test": True})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): m.save(p, {})

    def test_seed_plan_precedes_generation_failure_no_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "draft"
            def fail(*args):
                self.assertTrue((out / "seed-plan.json").exists())
                raise RuntimeError("synthetic construction failure")
            with patch.object(m, "OUT", out), patch.object(m, "historical_seeds", return_value=(set(), {})), patch.object(m.spark_world, "generate_spark_world", side_effect=fail) as gen:
                with self.assertRaises(RuntimeError): m.build()
                self.assertEqual(gen.call_count, 1)
                self.assertFalse((out / "audit.json").exists())


if __name__ == "__main__": unittest.main()
