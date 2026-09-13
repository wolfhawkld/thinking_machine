"""Independent descriptive supplement; does not change frozen primary analysis.

Run without --analysis for the pre-live baseline table. With --analysis, replay
the frozen analyzer and require equality before making model comparisons.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import spark_strong_k4_benchmark as support
from src import spark_strong_k4_utilization_primary_benchmark as benchmark
from src import spark_strong_k4_utilization_primary_live as live


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def baseline_rows(private):
    rows = []
    for policy in private["baseline_report"]["policies"]:
        results = []
        for pair in private["pairs"]:
            selected = {arm: support._select_baseline_raw_action(policy["policy_id"], pair["arms"][arm]["public_action_features"], pair["action_order"]) for arm in benchmark.ARMS}
            correct = {arm: pair["arms"][arm]["correct_raw_action_indices"][0] for arm in benchmark.ARMS}
            own = sum(selected[arm] == correct[arm] for arm in benchmark.ARMS)
            cross = int(selected["context_a"] == correct["context_b"]) + int(selected["context_b"] == correct["context_a"])
            results.append({"pair_ordinal": pair["pair_ordinal"], "stratum": pair["construction_stratum"], "complete_switch": own == 2, "own_hits": own, "signed_score": own - cross})
        assert sum(r["complete_switch"] for r in results) == policy["complete_switch_world_count"]
        assert sum(r["signed_score"] for r in results) == policy["signed_total"]
        rows.append({**policy, "own_context_hit_count": sum(r["own_hits"] for r in results), "pairs": results})
    return rows


def summarize(private, model_pairs=None):
    n = len(private["pairs"])
    policies = baseline_rows(private)
    report = {"kind": "utilization-descriptive-supplement", "new_inferential_tests": False,
              "world_layer": "outcome_conditioned_development_only", "world_denominator": n,
              "call_denominator": 2 * n, "model_results_available": model_pairs is not None,
              "provider_calls_made_by_supplement": 0, "policies": []}
    model = None
    if model_pairs is not None:
        model = {r["pair_ordinal"]: r for r in model_pairs}
        if len(model_pairs) != n or set(model) != {p["pair_ordinal"] for p in private["pairs"]}:
            raise ValueError("model pair set must equal the complete fixed cohort")
        strata = {}
        for p in private["pairs"]:
            r = model[p["pair_ordinal"]]
            counts = strata.setdefault(p["construction_stratum"], Counter(world_count=0, complete_switch=0, paired_valid_own_hits=0, invalid_worlds=0))
            counts["world_count"] += 1
            counts["complete_switch"] += int(r["complete_two_arm_context_concordant_switch"])
            counts["paired_valid_own_hits"] += r["own_context_correct_count"]
            counts["invalid_worlds"] += int(r["received_invalid_world"])
        report["model"] = {"complete_switch_count": sum(r["complete_two_arm_context_concordant_switch"] for r in model.values()),
                           "paired_valid_own_hit_count": sum(r["own_context_correct_count"] for r in model.values()),
                           "invalid_world_count": sum(r["received_invalid_world"] for r in model.values()),
                           "strata": strata}
        report["own_hit_definition"] = "same as frozen scorer: either arm invalid makes both own-hit indicators zero; not per-call validity accuracy"
    for policy in policies:
        row = {k: v for k, v in policy.items() if k != "pairs"}
        if model is not None:
            counts = Counter(model_only=0, policy_only=0, both=0, neither=0)
            pairs = []
            for r in policy["pairs"]:
                m = bool(model[r["pair_ordinal"]]["complete_two_arm_context_concordant_switch"])
                b = r["complete_switch"]
                category = "both" if m and b else "model_only" if m else "policy_only" if b else "neither"
                counts[category] += 1
                pairs.append({"pair_ordinal": r["pair_ordinal"], "category": category})
            row["paired_complete_switch_comparison"] = dict(counts)
            row["model_minus_policy_complete_switch_count"] = counts["model_only"] - counts["policy_only"]
            row["paired_categories"] = pairs
        report["policies"].append(row)
    return report


def load_bound_private():
    config = live.load_frozen_config()
    binding = config["benchmark_binding"]
    path = ROOT / binding["private_key_relative_path"]
    if sha(path) != binding["private_key_file_sha256"]:
        raise ValueError("private key differs from frozen live binding")
    return config, path, json.loads(path.read_text())


def validated_model_pairs(config, private_path, analysis_path):
    saved = json.loads(analysis_path.read_text())
    folder = ROOT / config["artifact_contract"]["output_directory"]
    replay = live.analyze_primary(
        plan_path=folder / "plan.json", expected_plan_file_sha256=saved["live_plan_file_sha256"],
        canary_path=folder / "deepseek-pro-canary.json", expected_canary_file_sha256=saved["canary_file_sha256"],
        authorization_path=folder / "authorization.json", expected_authorization_file_sha256=saved["authorization_file_sha256"],
        public_manifest_path=ROOT / config["benchmark_binding"]["public_manifest_relative_path"],
        generation_path=folder / "generation.json", expected_generation_file_sha256=saved["generation_file_sha256"],
        private_key_path=private_path)
    if saved != replay or replay.get("primary_evaluable") is not True:
        raise ValueError("requires exactly replayed complete evaluable primary analysis")
    return replay["pair_results"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config, private_path, private = load_bound_private()
    pairs = validated_model_pairs(config, private_path, args.analysis) if args.analysis else None
    report = summarize(private, pairs)
    report["private_key_file_sha256"] = sha(private_path)
    report["supplement_source_sha256"] = sha(__file__)
    if args.analysis:
        report["formal_analysis_file_sha256"] = sha(args.analysis)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Saved {args.output}; model_results_available={pairs is not None}")
