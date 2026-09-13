import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import new_evidence_live_analysis_20260910 as m
import new_evidence_scoring_20260910 as scoring


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fixture(root):
    tasks = []
    bindings = []
    conditions = scoring.CONDITIONS
    target = [int(point[0] > 0) for point in scoring.dsl.DOMAIN]
    test = [{"point": list(point), "label": target[index]}
            for index, point in enumerate(scoring.dsl.DOMAIN[1:65], 1)]
    for ordinal in range(9):
        for condition in conditions:
            task_id = f"task-{ordinal}-{condition}"
            prompt = f"synthetic prompt {task_id}"
            prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
            tasks.append({"task_id": task_id, "rendered_prompt": prompt,
                          "prompt_sha256": prompt_sha})
            d0 = [{"point": list(scoring.dsl.DOMAIN[0]), "label": target[0]}]
            bindings.append({
                "task_id": task_id,
                "prompt_sha256": prompt_sha,
                "ordinal": ordinal,
                "condition": condition,
                "D0": d0,
                "visible_observations": d0,
                "test": copy.deepcopy(test),
                "target_behavior": target,
            })

    private_path = root / "private.json"
    private = {"bindings": bindings}
    private_path.write_text(json.dumps(private), encoding="utf-8")
    baseline_path = root / "code-baselines.json"
    baselines = {}
    for offset, name in enumerate(m.BASELINE_NAMES):
        baselines[name] = {
            "conditions": {
                condition: {
                    "worlds": 9,
                    "mean_test_accuracy": 0.40 + offset * 0.05 + index * 0.01,
                }
                for index, condition in enumerate(conditions)
            },
            "contrasts": {},
            "new_inferential_tests": False,
        }
    baseline_path.write_text(json.dumps(baselines), encoding="utf-8")
    plan = {
        "kind": "synthetic",
        "formal_calls": 27,
        "maximum_calls": 29,
        "max_technical_retries": 2,
        "conditions": list(conditions),
        "tasks": tasks,
        "private_file_sha256": _sha(private_path),
        "source_private_file_sha256": _sha(private_path),
        "code_baselines_file_sha256": _sha(baseline_path),
    }
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan, private, tasks, plan_path, private_path, baseline_path


def _record(task, expression="(const 0)", *, finish="stop", reasoning=True,
            usage=(10, 5), latency=12):
    return {
        "task_id": task["task_id"],
        "prompt_sha256": task["prompt_sha256"],
        "content": json.dumps({"expression": expression}) if expression is not None else None,
        "valid_choice": expression in {"(const 0)", "(const 1)"},
        "response": {
            "input_tokens": usage[0] if usage else None,
            "output_tokens": usage[1] if usage else None,
            "latency_ms": latency,
            "provider_model": "synthetic-model",
            "finish_reason": finish,
        } if usage is not None else None,
        "thinking_telemetry": {
            "reasoning_content_present": reasoning,
            "output_truncated": finish == "length",
        } if reasoning is not None else {},
    }


class _Transport:
    @staticmethod
    def sha(path):
        return _sha(path)

    @staticmethod
    def _read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def _write_exclusive_json(path, value):
        target = Path(path)
        descriptor = target.open("x", encoding="utf-8")
        with descriptor:
            json.dump(value, descriptor, sort_keys=True)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.plan, self.private, self.tasks, self.plan_path,
         self.private_path, self.baseline_path) = _fixture(self.root)
        self.generation_path = self.root / "generation.json"
        self.analysis_path = self.root / "analysis.json"
        self.report_path = self.root / "results.md"
        records = []
        slots = []
        for index, task in enumerate(self.tasks):
            condition = task["task_id"].split("-")[-1]
            expression = "(const 1)" if condition == "new_evidence" else "(const 0)"
            record = _record(task, expression, reasoning=condition != "repeat_evidence")
            records.append(record)
            slots.append({"index": index, "task_id": task["task_id"],
                          "prompt_sha256": task["prompt_sha256"],
                          "status": "response", "record": record})
        self.generation = {
            "plan_file_sha256": _sha(self.plan_path),
            "formal_calls": 27,
            "provider_calls": 27,
            "records": records,
            "slots": slots,
            "technical_failures": [],
            "known_response_usage": {"input_tokens": 270, "output_tokens": 135},
            "usage_complete": True,
        }
        self.generation_path.write_text(json.dumps(self.generation), encoding="utf-8")
        self.fake_live = mock.Mock()
        self.fake_live.validate.return_value = (self.plan, self.tasks, _Transport())
        self.live_patch = mock.patch.object(m, "live", self.fake_live)
        self.live_patch.start()

    def tearDown(self):
        self.live_patch.stop()
        self.temp.cleanup()

    def _analyze(self, **kwargs):
        return m.analyze(self.plan_path, self.private_path, self.generation_path,
                         self.analysis_path, baseline_path=self.baseline_path,
                         report_path=self.report_path, **kwargs)

    def test_three_means_d0_contrasts_and_metadata(self):
        result = self._analyze(write=False)
        self.assertEqual(set(result["conditions"]), set(scoring.CONDITIONS))
        self.assertEqual(result["conditions"]["baseline"]["D0_consistent"], 9)
        self.assertEqual(result["conditions"]["new_evidence"]["D0_consistent"], 0)
        self.assertEqual(result["conditions"]["repeat_evidence"]["D0_consistent"], 9)
        self.assertEqual(result["contrasts"]["new_evidence_vs_baseline"]["worlds"], 9)
        self.assertEqual(result["conditions"]["baseline"]["usage"]["input_tokens"], 90)
        self.assertEqual(result["conditions"]["baseline"]["finish_reason_counts"], {"stop": 9})
        self.assertEqual(result["conditions"]["repeat_evidence"]["reasoning"]["content_present"], 0)
        self.assertEqual(set(result["code_baselines"]), set(m.BASELINE_NAMES))
        self.assertEqual(result["code_baselines_file_sha256"], _sha(self.baseline_path))
        self.assertIsNone(result["p_values"])

    def test_slots_and_prompt_bindings_are_fixed_but_missing_is_scored(self):
        generation = copy.deepcopy(self.generation)
        missing_id = self.tasks[0]["task_id"]
        generation["records"] = generation["records"][1:]
        generation["known_response_usage"]["input_tokens"] -= 10
        generation["known_response_usage"]["output_tokens"] -= 5
        self.generation_path.write_text(json.dumps(generation), encoding="utf-8")
        result = self._analyze(write=False)
        baseline = result["conditions"]["baseline"]
        self.assertEqual(baseline["missing"], 1)
        self.assertEqual(baseline["invalid"], 0)
        self.assertEqual(result["missing_or_unparsed_tasks"], 1)
        self.assertEqual(len(generation["slots"]), 27)
        self.assertNotIn(missing_id, {r["task_id"] for r in generation["records"]})

    def test_invalid_content_and_non_text_content_remain_scorer_misses(self):
        generation = copy.deepcopy(self.generation)
        generation["records"][0]["content"] = "not-json"
        generation["records"][3]["content"] = []
        self.generation_path.write_text(json.dumps(generation), encoding="utf-8")
        result = self._analyze(write=False)
        baseline = result["conditions"]["baseline"]
        self.assertEqual(baseline["invalid"], 2)
        self.assertEqual(baseline["valid_programs"], 7)

    def test_generation_and_frozen_baseline_hashes_fail_closed(self):
        generation = copy.deepcopy(self.generation)
        generation["plan_file_sha256"] = "0" * 64
        self.generation_path.write_text(json.dumps(generation), encoding="utf-8")
        with self.assertRaises(ValueError):
            self._analyze(write=False)
        self.generation_path.write_text(json.dumps(self.generation), encoding="utf-8")
        self.plan["code_baselines_file_sha256"] = "0" * 64
        self.fake_live.validate.return_value = (self.plan, self.tasks, _Transport())
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        with self.assertRaises(ValueError):
            self._analyze(write=False)

    def test_initial_write_is_exclusive_and_verify_is_read_only(self):
        result = self._analyze(write=True)
        self.assertEqual(result["tasks"], 27)
        self.assertTrue(self.analysis_path.is_file())
        self.assertTrue(self.report_path.is_file())
        analysis_mtime = self.analysis_path.stat().st_mtime_ns
        report_mtime = self.report_path.stat().st_mtime_ns
        with self.assertRaises(FileExistsError):
            self._analyze(write=True)
        self.assertEqual(self.analysis_path.stat().st_mtime_ns, analysis_mtime)
        self.assertEqual(self.report_path.stat().st_mtime_ns, report_mtime)
        self.assertIn("nine previously used development worlds", m.render_report(result))
        self.assertIn("not unrelated noise", m.render_report(result))
        self.assertIn("No K4/action-switch score", m.render_report(result))


if __name__ == "__main__":
    unittest.main()
