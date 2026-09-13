"""Post-hoc public-feature explanation of nine discordant worlds; no LLM calls."""
from collections import Counter
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import importlib.util

HERE = Path(__file__).resolve()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


a = module("analysis", HERE.with_name("analyze-thinking-staggered-20260909.py"))
base = a.base
support = module("supplement", HERE.with_name("utilization-supplement-20260908.py"))
from src import dsl, spark_lineage

NOVELTY = "public-k1-max-parent-novelty-node-hash"


def select_nonconstant(actions, order, mode):
    # New diagnostic policies. Input is target-free; no correct-action argument.
    choices = [x for x in actions if x["public_features"]["K1_supported"]
               and x["public_features"]["child_behavior_is_constant"] is False]
    if not choices:
        return support.support._select_baseline_raw_action(NOVELTY, actions, order)
    def rank(x):
        f = x["public_features"]
        prefix = (-f["parent_behavior_novelty_count"],) if mode == "novelty" else ()
        return prefix + (f["node_count"], f["child_canonical_hash"], x["raw_action_index"])
    return min(choices, key=rank)["raw_action_index"]


def verify_supported_features(prompt, features):
    parent = dsl.parse_sexpr(prompt.split("Frozen parent:\n", 1)[1].split("\n", 1)[0])
    motif = dsl.parse_sexpr(prompt.split("Context fragment:\n", 1)[1].split("\n", 1)[0])
    parent_behavior = dsl.behavior_vector(parent)
    for row in features:
        f = row["public_features"]
        if not f["K1_supported"]:
            continue  # K1 eligibility itself remains frozen; only supported features replayed here.
        edit = support.support._semantic_action(row["raw_action_index"])
        old = spark_lineage.get_subtree(parent, edit.path)
        inserted = motif if edit.operation == "replace" else (
            (edit.binary_operator, motif, old) if edit.motif_side == "left" else (edit.binary_operator, old, motif))
        child = dsl.canonicalize(spark_lineage._replace_subtree(parent, edit.path, inserted))
        behavior = dsl.behavior_vector(child)
        observed = {"K1_supported": True, "full_domain_positive_count": sum(behavior),
                    "node_count": dsl.node_count(child), "child_canonical_hash": dsl.canonical_hash(child),
                    "child_behavior_hash": dsl.behavior_hash(child),
                    "parent_behavior_novelty_count": sum(x != y for x, y in zip(behavior, parent_behavior)),
                    "child_behavior_is_constant": len(set(behavior)) == 1}
        if observed != f:
            raise ValueError("prompt-derived features differ from frozen public features")


def audit():
    saved = base.read(a.m.OUT / "analysis.json")
    with patch.object(base, "save") as capture, contextlib.redirect_stdout(io.StringIO()):
        a.analyze()
    if capture.call_args.args[1] != saved:
        raise ValueError("previous analysis replay mismatch")
    _, old, _ = a.m.checked_plan()
    private = base.read(base.ROOT / old["benchmark_binding"]["private_key_relative_path"])
    generation = base.read(a.m.OUT / "generation.json")
    by_task = {r["task_id"]: r for r in generation["records"]}
    prompts = {t["task_id"]: t["rendered_prompt"] for t in old["tasks"]}
    policy_rows = {p["policy_id"]: p for p in saved["all_baseline_comparisons"]["policies"]}
    categories = {r["pair_ordinal"]: r["category"] for r in policy_rows[NOVELTY]["paired_categories"]}
    baseline_sets = {p["policy_id"]: {r["pair_ordinal"] for r in p["paired_categories"] if r["category"] in ("both", "policy_only")}
                     for p in policy_rows.values()}
    cases, all_worlds, counts = [], [], Counter()
    diagnostics = {mode: {"own": 0, "cross": 0, "complete_switch": 0, "favorable": 0, "adverse": 0, "tie": 0,
                          "model_only": 0, "policy_only": 0, "both": 0, "neither": 0, "complete_pair_ordinals": []}
                   for mode in ("novelty", "minnode")}
    for pair in private["pairs"]:
        ordinal = pair["pair_ordinal"]
        rows = []
        selections = {mode: {} for mode in diagnostics}
        correct = {arm: v["correct_raw_action_indices"][0] for arm, v in pair["arms"].items()}
        for arm, v in pair["arms"].items():
            actions = v["public_action_features"]
            verify_supported_features(prompts[v["task_id"]], actions)
            fs = {x["raw_action_index"]: x["public_features"] for x in actions}
            model = pair["option_to_raw_action"][by_task[v["task_id"]]["selected_option_id"]]
            baseline = support.support._select_baseline_raw_action(NOVELTY, actions, pair["action_order"])
            nc = [k for k, f in fs.items() if f["K1_supported"] and f["child_behavior_is_constant"] is False]
            counts["single_nonconstant_arms"] += len(nc) == 1
            counts["correct_is_nonconstant"] += correct[arm] in nc
            reason = "same_action"
            if model != baseline:
                if fs[baseline]["child_behavior_is_constant"] is True and fs[model]["child_behavior_is_constant"] is False:
                    reason = "baseline_constant_model_nonconstant"
                elif fs[model]["parent_behavior_novelty_count"] == fs[baseline]["parent_behavior_novelty_count"] and fs[model]["node_count"] == fs[baseline]["node_count"]:
                    reason = "novelty_and_nodes_tie_hash_break"
                else:
                    reason = "model_lower_novelty"
            rows.append({"arm": arm, "model_raw": model, "correct_raw": correct[arm], "baseline_raw": baseline,
                         "model_correct": model == correct[arm], "baseline_correct": baseline == correct[arm],
                         "reason": reason, "nonconstant_candidate_count": len(nc),
                         "model_features": fs[model], "baseline_features": fs[baseline],
                         "correct_features": fs[correct[arm]]})
            for mode in diagnostics:
                selections[mode][arm] = select_nonconstant(actions, pair["action_order"], mode)
        model_complete = all(r["model_correct"] for r in rows)
        for mode, selected in selections.items():
            o = sum(selected[arm] == correct[arm] for arm in correct)
            c = int(selected["context_a"] == correct["context_b"]) + int(selected["context_b"] == correct["context_a"])
            d = diagnostics[mode]
            d["own"] += o; d["cross"] += c; d["complete_switch"] += o == 2
            d["favorable" if o > c else "adverse" if o < c else "tie"] += 1
            d["both" if model_complete and o == 2 else "model_only" if model_complete else "policy_only" if o == 2 else "neither"] += 1
            if o == 2: d["complete_pair_ordinals"].append(ordinal)
        world = {"pair_ordinal_zero_based": ordinal, "stratum": pair["construction_stratum"],
                 "category": categories[ordinal], "arms": rows,
                 "other_frozen_policies_complete": [name for name, pairs in baseline_sets.items() if name != NOVELTY and ordinal in pairs]}
        all_worlds.append(world)
        if categories[ordinal] in ("model_only", "policy_only"):
            cases.append(world)
    report = {"kind": "posthoc_case_audit", "provider_calls": 0, "new_inferential_tests": False,
              "policy_additions_are_posthoc_not_frozen_baselines": True,
              "source_sha256": base.sha(HERE), "analysis_sha256": base.sha(a.m.OUT / "analysis.json"),
              "feature_replay": "all supported-action features reproduced from public parent/context over 125 inputs; K1 eligibility reused frozen",
              "counts": dict(counts), "cases": cases, "all_worlds": all_worlds,
              "posthoc_nonconstant_diagnostics": diagnostics}
    base.save(a.m.OUT / "case-audit.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("cases", "all_worlds")}, indent=2))
    for case in cases:
        print(json.dumps({"pair": case["pair_ordinal_zero_based"], "category": case["category"],
                          "other_complete": case["other_frozen_policies_complete"],
                          "disagreements": [{"arm": r["arm"], "reason": r["reason"], "model_correct": r["model_correct"]}
                                            for r in case["arms"] if r["model_raw"] != r["baseline_raw"]]}, ensure_ascii=False))


if __name__ == "__main__":
    audit()
