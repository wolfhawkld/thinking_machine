"""Offline GLM merged-response scoring and descriptive cross-model comparisons."""
from collections import Counter
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = module("retry", HERE.with_name("glm53-retry-merge-20260909.py"))
base = m.base


def validated_generation():
    plan, old, original = m.checked_plan()
    retry = base.read(m.OUT / "retry-response.json")
    g = base.read(m.OUT / "generation.json")
    results, records = m.merge(old, original, retry)
    if (g["complete"] is not True or g["results"] != results or g["records"] != records
        or g["plan_file_sha256"] != base.sha(m.OUT / "plan.json")
        or g["old_failure_file_sha256"] != base.sha(m.live.OUT / "failure.json")
        or g["retry_response_file_sha256"] != base.sha(m.OUT / "retry-response.json")
        or g["retained_original_response_count"] != 47 or g["retried_index_zero_based"] != 24
        or g["known_task_physical_attempts"] != 49 or g["usage_complete"] is not False):
        raise ValueError("merged bundle provenance mismatch")
    ledger = [json.loads(s) for s in (m.OUT / "attempts.jsonl").read_text().splitlines()]
    if (ledger[0]["plan_file_sha256"] != g["plan_file_sha256"] or ledger[-1]["event"] != "complete"
        or len([e for e in ledger if e["event"] == "attempt_started"]) != 1):
        raise ValueError("retry ledger mismatch")
    for task, result, record in zip(old["tasks"], results, records):
        t = result["telemetry"]
        if (result["failure"] is not None or t["http_status"] != 200 or t["curl_exit"] != 0
            or t["done"] is not True or t["reported_usage"] is None):
            raise ValueError("incomplete accepted stream")
        r = record["response"]
        if r["provider_model"] != "glm-5.3" or r["finish_reason"] != t["finish_reason"]:
            raise ValueError("model/finish mismatch")
        if r["input_tokens"] != t["reported_usage"]["prompt_tokens"] or r["output_tokens"] != t["reported_usage"]["completion_tokens"]:
            raise ValueError("stream usage mismatch")
        ids = base.live._option_ids_from_prompt(task["rendered_prompt"])
        if record["valid_choice"]:
            if (record["selected_option_id"] not in ids or r["candidate_format"] != "json_expression"
                or r["candidate_expression"] != record["selected_option_id"]):
                raise ValueError("valid option mismatch")
        elif record["selected_option_id"] is not None or record["invalid_reason"] is None:
            raise ValueError("invalid option accounting mismatch")
    usage = {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")}
    if usage != g["known_response_usage"]:
        raise ValueError("aggregate usage mismatch")
    return plan, old, g


def compare(left, right):
    right = {r["pair_ordinal"]: r for r in right}
    counts = Counter(glm_only=0, comparator_only=0, both=0, neither=0)
    for r in left:
        a = r["complete_two_arm_context_concordant_switch"]
        b = right[r["pair_ordinal"]]["complete_two_arm_context_concordant_switch"]
        counts["both" if a and b else "glm_only" if a else "comparator_only" if b else "neither"] += 1
    return dict(counts)


def metrics(rows):
    return {"own": sum(r["own_context_correct_count"] for r in rows),
            "cross": sum(r["cross_context_correct_count"] for r in rows),
            "complete_switch": sum(r["complete_two_arm_context_concordant_switch"] for r in rows),
            "favorable": sum(r["classification"] == "favorable" for r in rows),
            "adverse": sum(r["classification"] == "adverse" for r in rows),
            "tie": sum(r["classification"] == "tie" for r in rows)}


def analyze():
    plan, old, g = validated_generation()
    binding = old["benchmark_binding"]
    key = base.ROOT / binding["private_key_relative_path"]
    if base.sha(key) != binding["private_key_file_sha256"]:
        raise ValueError("scoring key mismatch")
    private = base.read(key)
    records = g["records"]
    by_task = {r["task_id"]: r for r in records}
    rows, totals, strata = base.live._score_private_pairs(private, by_task)
    supplement = module("supplement", HERE.with_name("utilization-supplement-20260908.py"))
    policies = supplement.summarize(private, rows)
    audit = module("audit", HERE.with_name("thinking-case-audit-20260909.py"))
    diagnostic = {}
    for mode in ("novelty", "minnode"):
        synthetic = {}
        same = 0
        for pair in private["pairs"]:
            raw_to_option = {v: k for k, v in pair["option_to_raw_action"].items()}
            for arm, v in pair["arms"].items():
                raw = audit.select_nonconstant(v["public_action_features"], pair["action_order"], mode)
                option = raw_to_option[raw]
                synthetic[v["task_id"]] = {"valid_choice": True, "selected_option_id": option}
                same += int(by_task[v["task_id"]]["selected_option_id"] == option)
        ds, _, _ = base.live._score_private_pairs(private, synthetic)
        diagnostic[mode] = {**metrics(ds), "glm_complete_switch_comparison": compare(rows, ds),
                            "same_action_calls": same, "origin": "posthoc_DeepSeek_diagnostic_specified_before_GLM_outputs"}
    deepseek = {}
    sources = {"disabled": base.OLD / "analysis.json",
               "thinking": base.ROOT / "artifacts/deepseek-thinking-128k-staggered-20260909/analysis.json"}
    for name, path in sources.items():
        prior = base.read(path)["pair_results"]
        deepseek[name] = {**metrics(prior), "glm_complete_switch_comparison": compare(rows, prior),
                         "source_file_sha256": base.sha(path)}
    report = {"kind": "glm53_descriptive_cross_model_analysis", "evidence_scope": plan["evidence_scope"],
              "new_inferential_tests": False, "independent_world_count": 24,
              "world_sampling": "fixed_outcome_conditioned_development_cohort_not_natural_or_heldout_sample",
              "source_file_sha256": base.sha(HERE), "generation_file_sha256": base.sha(m.OUT / "generation.json"),
              "plan_file_sha256": base.sha(m.OUT / "plan.json"), "private_key_file_sha256": base.sha(key),
              "metrics": metrics(rows), "totals": totals, "strata": strata, "pair_results": rows,
              "valid_calls": sum(r["valid_choice"] for r in records),
              "truncated_calls": sum(r["response"]["finish_reason"] == "length" for r in records),
              "reasoning_present_calls": sum(r["thinking_telemetry"]["reasoning_content_present"] for r in records),
              "known_response_usage": g["known_response_usage"], "usage_complete": False,
              "known_task_physical_attempts": 49, "retained_prior_response_count": 47,
              "deepseek_comparisons": deepseek, "all_24_frozen_policies": policies,
              "separately_labelled_nonconstant_diagnostics": diagnostic,
              "retry_affected_pair": next(r for r in rows if g["records"][24]["task_id"] in r["task_ids"].values())}
    base.save(m.OUT / "analysis.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("pair_results", "all_24_frozen_policies")}, indent=2))
    print(json.dumps([{k:v for k,v in p.items() if k != 'paired_categories'} for p in policies['policies'][-4:]], indent=2))


if __name__ == '__main__': analyze()
