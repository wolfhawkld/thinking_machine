"""Replay only the selected development cohort at four/five rounds, offline."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import dsl, spark_lineage as lineage
from src import spark_strong_k4_scan as scan
from src import spark_strong_k4_utilization_feasibility as feasibility
from src.spark_world import generate_spark_world
from src.spark_compressor import SparkCompressor
from src.provenance import source_manifest


def outcome(result):
    return {"exact_identification": result.exact_identification, "N_T": result.N_T,
            "rounds_completed": result.rounds_completed, "truth_retained": result.truth_retained}


def budget_closure(result, direct, budget):
    """K2-like diagnostic with an explicit horizon; not a new frozen K2 label."""
    return bool(not direct and result.exact_identification
                and any(not step.response.is_match and step.N_after < step.N_before for step in result.steps)
                and result.rounds_completed <= budget)


if __name__ == "__main__":
    config_path = ROOT / "configs/spark-strong-k4-utilization-feasibility-v2.json"
    config = json.loads(config_path.read_text())
    live = json.loads((ROOT / "configs/spark-strong-k4-utilization-primary-live-v1.json").read_text())
    private_path = ROOT / live["benchmark_binding"]["private_key_relative_path"]
    private_sha = hashlib.sha256(private_path.read_bytes()).hexdigest()
    if private_sha != live["benchmark_binding"]["private_key_file_sha256"]:
        raise ValueError("private key differs from selected cohort binding")
    private = json.loads(private_path.read_text())
    totals = Counter(worlds=0, parent_identified_at_4=0, parent_identified_at_5=0,
                     correct_children=0, correct_child_identified_at_4=0, correct_child_identified_at_5=0,
                     supported_actions=0, additional_K2_actions_at_5=0)
    rows = []
    for pair in private["pairs"]:
        seed = pair["world_binding"]["world_seed"]
        # Exact replay of an already opened, selected development target; no redraw/selection.
        world = generate_spark_world(seed, target_seed=feasibility.derive_private_target_seed(config, seed))
        parent = lineage.select_parent(world)
        assert dsl.canonical_hash(parent) == pair["world_binding"]["parent_canonical_hash"]
        compressor = SparkCompressor(world)
        cache = {}

        def run(ast, rounds):
            key = (dsl.behavior_vector(ast, compressor.domain), rounds)
            if key not in cache:
                cache[key] = compressor.run(ast, max_rounds=rounds)
            return cache[key]

        p4, p5 = run(parent, 4), run(parent, 5)
        totals["worlds"] += 1
        totals["parent_identified_at_4"] += p4.exact_identification
        totals["parent_identified_at_5"] += p5.exact_identification
        arm_rows = []
        for arm in pair["arms"].values():
            motif_id = arm["motif"]["motif_id"]
            motif = scan.motif_by_id(motif_id)
            actions = scan._raw_actions(world, motif_id)
            k2 = Counter(at_4=0, at_5=0)
            correct_result = None
            for feature in arm["public_action_features"]:
                if not feature["public_features"]["K1_supported"]:
                    continue
                raw = feature["raw_action_index"]
                child = lineage.apply_edit(parent, motif, actions[raw])
                assert dsl.canonical_hash(child) == feature["public_features"]["child_canonical_hash"]
                r4, r5 = run(child, 4), run(child, 5)
                assert r5.N_T <= r4.N_T and r4.truth_retained and r5.truth_retained
                direct = dsl.behavior_vector(child, compressor.domain) == compressor.target_behavior
                assert budget_closure(r4, direct, 4) == scan._is_k2(r4, child_direct_hit=direct)
                k2["at_4"] += budget_closure(r4, direct, 4)
                k2["at_5"] += budget_closure(r5, direct, 5)
                totals["supported_actions"] += 1
                if raw == arm["correct_raw_action_indices"][0]:
                    assert r4.exact_identification and not p4.exact_identification
                    correct_result = {"at_4": outcome(r4), "at_5": outcome(r5)}
                    totals["correct_children"] += 1
                    totals["correct_child_identified_at_4"] += r4.exact_identification
                    totals["correct_child_identified_at_5"] += r5.exact_identification
            assert correct_result is not None
            assert k2["at_4"] == pair["K2_opportunity_count_each_arm"]
            totals["additional_K2_actions_at_5"] += k2["at_5"] - k2["at_4"]
            arm_rows.append({"K2_counts": dict(k2), "original_correct_child": correct_result})
        rows.append({"pair_ordinal": pair["pair_ordinal"], "stratum": pair["construction_stratum"],
                     "parent_at_4": outcome(p4), "parent_at_5": outcome(p5), "contexts": arm_rows})
        print(f"Budget replay {len(rows)}/24 complete", flush=True)
    report = {"kind": "selected-development-budget-sensitivity-only", "model_outputs_read": False,
              "provider_calls_made": 0, "new_K4_labels_computed": False,
              "cohort_reselected": False, "max_rounds_compared": [4, 5],
              "K2_at_5_meaning": "diagnostic K2-like closure with five-round cap, not a replacement frozen K2 label",
              "private_key_file_sha256": private_sha,
              "source_manifest_sha256": source_manifest(ROOT)["source_manifest_sha256"],
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "config_file_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
              "totals": dict(totals), "pairs": rows}
    output = ROOT / "reviews/budget-sensitivity-20260908.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps(totals), flush=True)
