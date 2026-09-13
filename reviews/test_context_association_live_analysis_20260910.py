import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import context_association_live_analysis_20260910 as m


def _sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixture():
    """Small synthetic binding with the production 54/9/3 denominators."""

    tasks = []
    bindings = []
    for ordinal in range(m.WORLD_COUNT):
        for arm in ("context_a", "context_b"):
            original = f"original-{ordinal}-{arm}"
            for condition in m.CONDITIONS:
                task_id = f"{ordinal}-{arm}-{condition}"
                prompt = f"prompt:{task_id}"
                prompt_sha = _sha_text(prompt)
                tasks.append({
                    "task_id": task_id,
                    "rendered_prompt": prompt,
                    "prompt_sha256": prompt_sha,
                })
                bindings.append({
                    "task_id": task_id,
                    "original_task_id": original,
                    "condition": condition,
                    "prompt_sha256": prompt_sha,
                    "option_to_raw_action": {"A": 0, "B": 1, **{f"Q{i}": i for i in range(2, 10)}},
                    "candidate_hashes_by_raw": [f"hash-{i}" for i in range(10)],
                    "pair_ordinal": ordinal,
                    "arm": arm,
                    "correct_option_id": "A" if arm == "context_a" else "B",
                    "cross_option_id": "B" if arm == "context_a" else "A",
                })
    private = {
        "source_plan_sha256": _sha_text("source-plan"),
        "source_private_sha256": _sha_text("source-private"),
        "bindings": bindings,
    }
    return tasks, private


def _record(task, selected="A", valid=True, *, truncated=False, usage=(1, 2)):
    record = {
        "task_id": task["task_id"],
        "prompt_sha256": task["prompt_sha256"],
        "valid_choice": valid,
        "selected_option_id": selected if valid else None,
    }
    if truncated or usage is not None:
        record["thinking_telemetry"] = {
            "output_truncated": truncated,
            "reasoning_content_present": True,
        }
    if usage is not None:
        record["response"] = {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "finish_reason": "length" if truncated else "stop",
        }
    return record


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plan_path = self.root / "plan.json"
        self.private_path = self.root / "private.json"
        self.generation_path = self.root / "generation.json"
        self.tasks, self.private = _fixture()
        self.private_path.write_text(json.dumps(self.private), encoding="utf-8")
        self.plan = {
            "formal_calls": 54,
            "maximum_calls": 56,
            "max_technical_retries": 2,
            "conditions": list(m.CONDITIONS),
            "private_file_sha256": m.sha(self.private_path),
            "source_private_file_sha256": m.sha(self.private_path),
            "source_plan_sha256": self.private["source_plan_sha256"],
            "input_file_hashes": {
                str(m.HERE.relative_to(m.ROOT)): m.sha(m.HERE),
            },
            "tasks": self.tasks,
            "evidence_scope": "synthetic test",
        }
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        self.tasks_by_id = {task["task_id"]: task for task in self.tasks}

    def tearDown(self):
        self.temp.cleanup()

    def _write_generation(self, records):
        generation = {
            "plan_file_sha256": m.sha(self.plan_path),
            "formal_calls": 54,
            "provider_calls": len(records),
            "records": records,
            "known_response_usage": {
                "input_tokens": sum(r.get("response", {}).get("input_tokens", 0) for r in records),
                "output_tokens": sum(r.get("response", {}).get("output_tokens", 0) for r in records),
            },
            "usage_complete": len(records) == 54,
            "technical_failures": [],
        }
        self.generation_path.write_text(json.dumps(generation), encoding="utf-8")
        return generation

    def test_scores_three_conditions_and_fixed_world_contrasts(self):
        records = []
        for task in self.tasks:
            binding = next(row for row in self.private["bindings"] if row["task_id"] == task["task_id"])
            records.append(_record(task, binding["correct_option_id"]))
        self._write_generation(records)
        result = m.analyze(self.plan_path, self.private_path, self.generation_path, write=False)
        for condition in m.CONDITIONS:
            row = result["conditions_results"][condition]
            self.assertEqual((row["own"], row["cross"], row["full"], row["valid"], row["missing"], row["invalid"]),
                             (18, 0, 9, 18, 0, 0))
            self.assertEqual(len(row["world_rows"]), 9)
        for contrast in result["pairwise_world_contrasts"].values():
            self.assertEqual(contrast["worlds"], 9)
            self.assertEqual((contrast["own_wins"], contrast["own_losses"], contrast["own_ties"]), (0, 0, 9))
            self.assertEqual((contrast["full_wins"], contrast["full_losses"], contrast["full_ties"]), (0, 0, 9))
        self.assertFalse(result["new_inferential_tests"])

    def test_missing_invalid_truncated_and_usage_are_reported(self):
        records = []
        for task in self.tasks:
            binding = next(row for row in self.private["bindings"] if row["task_id"] == task["task_id"])
            if binding["condition"] == "no_reference" and binding["pair_ordinal"] == 8:
                continue
            if binding["condition"] == "aligned" and binding["pair_ordinal"] == 0 and binding["arm"] == "context_a":
                records.append(_record(task, valid=False, truncated=True, usage=(3, 4)))
            elif binding["condition"] == "unmatched" and binding["pair_ordinal"] == 0 and binding["arm"] == "context_a":
                records.append(_record(task, binding["correct_option_id"], usage=None))
            else:
                records.append(_record(task, binding["correct_option_id"]))
        self._write_generation(records)
        result = m.analyze(self.plan_path, self.private_path, self.generation_path, write=False)
        aligned = result["conditions_results"]["aligned"]
        self.assertEqual((aligned["valid"], aligned["invalid"], aligned["missing"], aligned["truncated"]), (17, 1, 0, 1))
        unmatched = result["conditions_results"]["unmatched"]
        self.assertEqual(unmatched["usage"]["input_token_records"], 17)
        no_reference = result["conditions_results"]["no_reference"]
        self.assertEqual((no_reference["valid"], no_reference["missing"]), (16, 2))
        self.assertEqual(result["missing_or_unparsed_tasks"], 2)

    def test_generation_binding_rejects_unknown_duplicate_and_prompt_drift(self):
        task = self.tasks[0]
        valid = _record(task)
        generation = {"plan_file_sha256": m.sha(self.plan_path), "records": [valid]}
        m.validate_generation(generation, self.plan_path, self.tasks_by_id)
        duplicate = {**generation, "records": [valid, copy.deepcopy(valid)]}
        with self.assertRaises(ValueError):
            m.validate_generation(duplicate, self.plan_path, self.tasks_by_id)
        unknown = {**generation, "records": [{**valid, "task_id": "unknown"}]}
        with self.assertRaises(ValueError):
            m.validate_generation(unknown, self.plan_path, self.tasks_by_id)
        drift = {**generation, "records": [{**valid, "prompt_sha256": _sha_text("drift")}]}
        with self.assertRaises(ValueError):
            m.validate_generation(drift, self.plan_path, self.tasks_by_id)

    def test_analysis_write_is_exclusive(self):
        records = []
        for task in self.tasks:
            binding = next(row for row in self.private["bindings"] if row["task_id"] == task["task_id"])
            records.append(_record(task, binding["correct_option_id"]))
        self._write_generation(records)
        output = self.root / "analysis.json"
        m.analyze(self.plan_path, self.private_path, self.generation_path, output)
        with self.assertRaises(FileExistsError):
            m.analyze(self.plan_path, self.private_path, self.generation_path, output)


if __name__ == "__main__":
    unittest.main()
