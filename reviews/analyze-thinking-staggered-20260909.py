"""Offline descriptive analysis of the disclosed mixed-schedule follow-up."""
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


m = module("migration", HERE.with_name("deepseek-thinking-128k-staggered-20260909.py"))
base = m.base


def validate_generation(p, old, preserved, g, events, response_loader):
    if (g.get("complete") is not True or g.get("failures") != []
        or g.get("evidence_scope") != p["evidence_scope"]
        or g.get("preserved_indices") != p["preserved_indices"]
        or g.get("new_dispatched_indices") != p["pending_indices"]
        or g.get("interrupted_prior_indices") != p["interrupted_prior_indices"]
        or g.get("known_physical_attempts") != p["maximum_total_physical_attempts"]
        or g.get("total_usage_complete") is not False):
        raise ValueError("requires complete disclosed migration bundle")
    if len(g["records"]) != 48:
        raise ValueError("requires exact 48 responses")
    submitted, started, saved = [], [], {}
    for event in events:
        kind = event["event"]
        if kind == "submitted":
            submitted.append(event["index"])
        elif kind == "attempt_started":
            i = event["index"]
            if i in started or i not in submitted or event["task_id"] != old["tasks"][i]["task_id"]:
                raise ValueError("start/task mismatch")
            started.append(i)
        elif kind == "response":
            i = event["index"]
            if i in saved or i not in started or response_loader(i) != event["record"]:
                raise ValueError("duplicate or inconsistent saved response")
            saved[i] = event["record"]
        elif kind not in ("start", "complete"):
            raise ValueError("unexpected failure/incomplete ledger")
    if submitted != p["pending_indices"] or sorted(started) != sorted(submitted) or sorted(saved) != sorted(submitted):
        raise ValueError("missing, duplicate or unscheduled request")
    if events[-1]["event"] != "complete" or events[-1]["saved_count"] != 48:
        raise ValueError("no complete ledger terminal")
    expected = {**preserved, **saved}
    for i, (record, task) in enumerate(zip(g["records"], old["tasks"])):
        if record != expected[i] or any(record[k] != task[k] for k in ("task_id", "prompt_sha256")):
            raise ValueError("record preservation/order mismatch")
        ids = base.live._option_ids_from_prompt(task["rendered_prompt"])
        r = record["response"]
        if (record["received"] is not True or type(record["valid_choice"]) is not bool
            or r["provider_model"] != m.serial.SETTINGS["model"]):
            raise ValueError("response contract mismatch")
        if record["valid_choice"]:
            if (record["selected_option_id"] not in ids or r["candidate_expression"] != record["selected_option_id"]
                or r["candidate_format"] != "json_expression" or record["invalid_reason"] is not None):
                raise ValueError("invalid option/validity record")
        elif record["selected_option_id"] is not None or record["invalid_reason"] is None:
            raise ValueError("invalid response not recorded consistently")
        for key in ("input_tokens", "output_tokens"):
            if type(r[key]) is not int or r[key] < 0:
                raise ValueError("invalid usage")
    usage = {k: sum(r["response"][k] for r in g["records"]) for k in ("input_tokens", "output_tokens")}
    if usage != g["known_response_usage"]:
        raise ValueError("usage aggregation mismatch")


def analyze():
    p, old, preserved = m.checked_plan()
    g = base.read(m.OUT / "generation.json")
    events = [json.loads(s) for s in (m.OUT / "attempts.jsonl").read_text().splitlines()]
    for obj in (g, events[0]):
        if obj["plan_file_sha256"] != base.sha(m.OUT / "plan.json"):
            raise ValueError("generation/ledger plan binding mismatch")
    validate_generation(p, old, preserved, g, events, lambda i: base.read(m.OUT / f"response-{i:02d}.json"))
    # All public-only provenance checks above precede opening the scoring key.
    binding = old["benchmark_binding"]
    key_path = base.ROOT / binding["private_key_relative_path"]
    if base.sha(key_path) != binding["private_key_file_sha256"]:
        raise ValueError("private key mismatch")
    private = base.read(key_path)
    records = g["records"]
    by_task = {r["task_id"]: r for r in records}
    rows, totals, strata = base.live._score_private_pairs(private, by_task)
    supplement = module("supplement", HERE.with_name("utilization-supplement-20260908.py"))
    policies = supplement.summarize(private, rows)
    # Agreement is descriptive, not evidence that the model used a policy.
    for policy in policies["policies"]:
        same = 0
        for pair in private["pairs"]:
            for arm in supplement.benchmark.ARMS:
                a = pair["arms"][arm]
                r = by_task[a["task_id"]]
                chosen = supplement.support._select_baseline_raw_action(policy["policy_id"], a["public_action_features"], pair["action_order"])
                same += int(r["valid_choice"] and pair["option_to_raw_action"][r["selected_option_id"]] == chosen)
        policy["model_same_action_calls"] = same
    prior = base.read(base.OLD / "analysis.json")["pair_results"]
    prior_by_pair = {r["pair_ordinal"]: r for r in prior}
    cross = Counter(thinking_only=0, disabled_only=0, both=0, neither=0)
    for r in rows:
        a = r["complete_two_arm_context_concordant_switch"]
        b = prior_by_pair[r["pair_ordinal"]]["complete_two_arm_context_concordant_switch"]
        cross["both" if a and b else "thinking_only" if a else "disabled_only" if b else "neither"] += 1
    report = {"kind": "descriptive_amended_thinking_analysis", "evidence_scope": p["evidence_scope"],
              "new_inferential_tests": False, "old_primary_replaced": False,
              "validation": "public schedule, saved serial prefix, per-response files, request ledger and provenance matched before private scoring",
              "source_file_sha256": base.sha(HERE), "plan_file_sha256": base.sha(m.OUT / "plan.json"),
              "generation_file_sha256": base.sha(m.OUT / "generation.json"),
              "ledger_file_sha256": base.sha(m.OUT / "attempts.jsonl"),
              "private_key_file_sha256": base.sha(key_path),
              "old_analysis_file_sha256": base.sha(base.OLD / "analysis.json"),
              "totals": totals, "strata": strata, "pair_results": rows,
              "paired_valid_own_hits": sum(r["own_context_correct_count"] for r in rows),
              "paired_valid_cross_hits": sum(r["cross_context_correct_count"] for r in rows),
              "valid_calls": sum(r["valid_choice"] for r in records),
              "truncated_calls": sum(r["response"]["finish_reason"] == "length" for r in records),
              "reasoning_present_calls": sum(r["thinking_telemetry"]["reasoning_content_present"] for r in records),
              "known_response_usage": g["known_response_usage"], "total_usage_complete": False,
              "known_physical_attempts": g["known_physical_attempts"],
              "old_new_complete_switch": dict(cross), "all_baseline_comparisons": policies}
    base.save(m.OUT / "analysis.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("pair_results", "all_baseline_comparisons")}, indent=2))
    print(json.dumps([{k: v for k, v in row.items() if k != "paired_categories"} for row in policies["policies"]], indent=2))


if __name__ == "__main__":
    analyze()
