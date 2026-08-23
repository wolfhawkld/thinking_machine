from __future__ import annotations

from dataclasses import fields
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from src import spark_lineage
from src import spark_strong_k4_benchmark as benchmark


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _context() -> dict[str, object]:
    points = [
        [index % 5 - 2, (index // 5) % 5 - 2, (index * 2) % 5 - 2]
        for index in range(12)
    ]
    return {
        "D0": [
            {"point": point, "label": int(sum(point) == 0)} for point in points
        ],
        "parent": "(if (eq x1 0) 1 0)",
        "old_subtrees": {"LEFT": "x1", "RIGHT": "0"},
    }


def _profile(
    motif_id: str,
    behavior: str,
    mask: tuple[bool, ...],
    *,
    canonical: str | None = None,
) -> benchmark.K1SupportProfile:
    return benchmark.K1SupportProfile(
        motif_id=motif_id,
        motif_sexpr=f"(add x1 {len(motif_id)})",
        motif_canonical_hash=_digest(canonical or motif_id),
        motif_behavior_hash=_digest(behavior),
        stratum=spark_lineage.MOTIF_STRATA[0],
        complexity_bucket=(2, 1),
        k1_mask=mask,
    )


def _audit_action(raw: int, *, k1: bool, k2: bool, k4: bool) -> dict[str, object]:
    return {
        "raw_action_index": raw,
        "endpoint_flags": {
            "K1": k1,
            "K2": k2,
            "K3": k2,
            "K4_full_pool": k4,
        },
    }


def _frozen_audit_pairs() -> list[dict[str, object]]:
    """Small endpoint-only fixture reproducing the frozen 53-frame audit."""

    distances = [0] * 20 + [1] * 6 + [2] * 4 + [3] * 2
    pairs: list[dict[str, object]] = []
    for ordinal in range(32):
        # 21 two-frame worlds and 11 one-frame worlds: 53 factual frames.
        two_frames = ordinal <= 17 or 19 <= ordinal <= 21
        factual_k4 = {0, 1} if two_frames else {0}
        # 18 two-frame worlds plus one one-frame world account for all 37
        # sham misses.  The remaining 16 frames pass K2.
        all_fail = ordinal <= 18
        factual: list[dict[str, object]] = []
        sham: list[dict[str, object]] = []
        for raw in range(10):
            is_frame = raw in factual_k4
            factual.append(
                _audit_action(raw, k1=is_frame, k2=is_frame, k4=is_frame)
            )
            invalid = ordinal < 3 and raw == 0
            sham_k1 = is_frame and not invalid
            sham_k2 = is_frame and not all_fail
            sham.append(
                _audit_action(raw, k1=sham_k1, k2=sham_k2, k4=False)
            )
        pairs.append(
            {
                "sham_selection_audit": {
                    "minimum_K1_mask_hamming_distance": distances[ordinal]
                },
                "arms": {"factual": {"actions": factual}, "sham": {"actions": sham}},
            }
        )
    return pairs


def _frozen_baseline_report() -> dict[str, object]:
    return {
        "B_star_policy_id": "public-k1-min-positive-node-hash",
        "B_star_factual_F_count": 19,
        "uniform_choice": {
            "factual_qualifying_action_counts": [2] * 21 + [1] * 11,
            "critical_factual_F_count": 10,
        },
        "policies": [
            {
                "policy_id": f"fixed-semantic-{raw:02d}",
                "factual_F_count": 14 if raw == 0 else 5,
            }
            for raw in range(10)
        ],
    }


def _joint_score(route_id: str, *, identity_suffix: str = "shared") -> dict[str, object]:
    p_value = {"numerator": 1, "denominator": 1000, "value": 0.001}
    paired = {
        "left_only_count": 12,
        "right_only_count": 1,
        "exact_one_sided_p_value": p_value,
    }
    unsigned: dict[str, object] = {
        "schema_version": benchmark.SCHEMA_VERSION,
        "kind": benchmark.OFFLINE_SCORE_KIND,
        "protocol_id": benchmark.PROTOCOL_ID,
        "model_id": route_id,
        "response_artifact_sha256": _digest(f"response-{route_id}"),
        "public_manifest_sha256": _digest(f"public-{identity_suffix}"),
        "private_key_sha256": _digest(f"private-{identity_suffix}"),
        "current_source_manifest_sha256": _digest(f"source-{identity_suffix}"),
        "received_response_count": 64,
        "invalid_response_count": 0,
        "pair_primary_U": paired,
        "factual_strong_F_count": 12,
        "factual_strong_breadth": {
            "construction_stratum_count": 3,
            "unique_child_behavior_count": 4,
            "nonconstant_child_hit_count": 2,
        },
        "versus_frozen_B_star": {
            "paired_test_model_greater_B_star": dict(paired)
        },
        "versus_uniform_choice": {
            "exact_Poisson_binomial_upper_tail": p_value,
            "exceeds_or_meets_critical_value": True,
        },
    }
    return {**unsigned, "score_sha256": benchmark._sha256_json(unsigned)}


class SealedInputTests(unittest.TestCase):
    def test_exact_historical_result_plan_and_config_load(self) -> None:
        result = benchmark.load_sealed_scan_result()
        self.assertEqual(result["scan_sha256"], benchmark.SEALED_SCAN_SHA256)
        self.assertEqual(len(result["worlds"]), 1024)

    def test_fair_protocol_config_matches_frozen_byte_seal(self) -> None:
        self.assertEqual(
            benchmark.fair_config_file_sha256(),
            benchmark.FAIR_CONFIG_FILE_SHA256,
        )

    def test_result_byte_tamper_is_rejected(self) -> None:
        payload = benchmark.SEALED_SCAN_PATH.read_bytes() + b"\n"
        path = Path(self.id().replace(".", "-"))
        # Use a temporary directory without obscuring the exact byte-seal check.
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_bytes(payload)
            with self.assertRaisesRegex(benchmark.FairChoiceError, "bytes differ"):
                benchmark.load_sealed_scan_result(path)


class FairMaskingTests(unittest.TestCase):
    def test_action_permutation_has_global_three_or_four_position_balance(self) -> None:
        counts = {raw: [0] * 10 for raw in range(10)}
        for ordinal in range(32):
            order = benchmark.action_order_for_pair(ordinal)
            self.assertEqual(set(order), set(range(10)))
            for position, raw in enumerate(order):
                counts[raw][position] += 1
        for position_counts in counts.values():
            self.assertEqual(set(position_counts), {3, 4})

    def test_opaque_ids_are_deterministic_pair_specific_and_ten_way(self) -> None:
        first = benchmark.option_ids_for_pair(_digest("pair-a"))
        second = benchmark.option_ids_for_pair(_digest("pair-b"))
        self.assertEqual(first, benchmark.option_ids_for_pair(_digest("pair-a")))
        self.assertEqual(len(first), 10)
        self.assertEqual(len(set(first)), 10)
        self.assertNotEqual(first, second)

    def test_paired_prompts_differ_only_in_context_fragment(self) -> None:
        anchor = _digest("prompt-pair")
        order = benchmark.action_order_for_pair(0)
        option_ids = benchmark.option_ids_for_pair(anchor)
        factual = "(add x1 1)"
        sham = "(sub x1 1)"
        left = benchmark.render_fair_choice_prompt(
            _context(), factual, order, option_ids
        )
        right = benchmark.render_fair_choice_prompt(_context(), sham, order, option_ids)
        self.assertEqual(left.replace(factual, "X"), right.replace(sham, "X"))
        self.assertEqual(sum(line.strip().startswith("Q") for line in left.splitlines()), 10)
        lowered = left.lower()
        for forbidden in benchmark._PROMPT_FORBIDDEN_TERMS:
            self.assertNotIn(forbidden, lowered)
        self.assertIn("fixed four-round verification", left)
        self.assertIn('Required output schema: {"expression":"<OPTION_ID>"}', left)

    def test_public_manifest_exposes_only_rendered_tasks(self) -> None:
        anchor = _digest("manifest-pair")
        prompt = benchmark.render_fair_choice_prompt(
            _context(),
            "(add x1 1)",
            benchmark.action_order_for_pair(0),
            benchmark.option_ids_for_pair(anchor),
        )
        tasks = [
            benchmark._task_record(f"TASK-{index:014d}", prompt)
            for index in range(64)
        ]
        manifest = benchmark._public_manifest(
            tasks, _digest("design"), _digest("source"), _digest("config")
        )
        benchmark.validate_public_manifest(manifest)
        self.assertTrue(
            all(
                set(task) == {"task_id", "rendered_prompt", "prompt_sha256"}
                for task in tasks
            )
        )
        encoded = json.dumps(manifest["tasks"], sort_keys=True).lower()
        for private_key in (
            "world_seed",
            "candidate_index",
            "option_to_raw_action",
            "endpoint_flags",
        ):
            self.assertNotIn(private_key, encoded)

    def test_blind_dispatch_sends_only_the_verified_prompt_string(self) -> None:
        anchor = _digest("blind-dispatch-pair")
        prompt = benchmark.render_fair_choice_prompt(
            _context(),
            "(add x1 1)",
            benchmark.action_order_for_pair(0),
            benchmark.option_ids_for_pair(anchor),
        )
        tasks = [
            benchmark._task_record(f"TASK-{index:014d}", prompt)
            for index in range(64)
        ]
        manifest = benchmark._public_manifest(
            tasks,
            _digest("blind-design"),
            _digest("blind-source"),
            _digest("blind-config"),
        )
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def provider_spy(*args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            return "provider-response"

        result = benchmark.dispatch_blind_public_task(
            manifest,
            tasks[7]["task_id"],
            provider_spy,
        )

        self.assertEqual(result, "provider-response")
        self.assertEqual(calls, [((prompt,), {})])
        self.assertNotIn(tasks[7]["task_id"], calls[0][0][0])

    def test_blind_dispatch_rejects_unknown_local_task_without_calling_provider(self) -> None:
        anchor = _digest("blind-dispatch-unknown")
        prompt = benchmark.render_fair_choice_prompt(
            _context(),
            "(add x1 1)",
            benchmark.action_order_for_pair(0),
            benchmark.option_ids_for_pair(anchor),
        )
        tasks = [
            benchmark._task_record(f"TASK-{index:014d}", prompt)
            for index in range(64)
        ]
        manifest = benchmark._public_manifest(
            tasks,
            _digest("unknown-design"),
            _digest("unknown-source"),
            _digest("unknown-config"),
        )
        called = False

        def provider_spy(prompt_text: str) -> None:
            nonlocal called
            called = True

        with self.assertRaisesRegex(benchmark.FairChoiceError, "absent"):
            benchmark.dispatch_blind_public_task(
                manifest,
                "TASK-NOT-IN-MANIFEST",
                provider_spy,
            )
        self.assertFalse(called)

    def test_blind_route_collection_calls_all_tasks_once_then_seals(self) -> None:
        anchor = _digest("blind-route-complete")
        prompt = benchmark.render_fair_choice_prompt(
            _context(),
            "(add x1 1)",
            benchmark.action_order_for_pair(0),
            benchmark.option_ids_for_pair(anchor),
        )
        tasks = [
            benchmark._task_record(f"TASK-{index:014d}", prompt)
            for index in range(64)
        ]
        manifest = benchmark._public_manifest(
            tasks,
            _digest("complete-design"),
            _digest("complete-source"),
            _digest("complete-config"),
        )
        received_prompts: list[str] = []

        def provider_spy(prompt_text: str) -> dict[str, object]:
            received_prompts.append(prompt_text)
            return {"candidate_format": "invalid_json", "expression": None}

        artifact = benchmark.collect_blind_route_response_artifact(
            manifest,
            route_id="deepseek-flash",
            send_prompt=provider_spy,
        )

        self.assertEqual(received_prompts, [prompt] * 64)
        self.assertEqual(artifact["task_count"], 64)
        self.assertEqual(artifact["route_id"], "deepseek-flash")
        self.assertEqual(artifact["transport_failure_count"], 0)

    def test_blind_route_transport_failure_propagates_without_retry(self) -> None:
        anchor = _digest("blind-route-failure")
        prompt = benchmark.render_fair_choice_prompt(
            _context(),
            "(add x1 1)",
            benchmark.action_order_for_pair(0),
            benchmark.option_ids_for_pair(anchor),
        )
        tasks = [
            benchmark._task_record(f"TASK-{index:014d}", prompt)
            for index in range(64)
        ]
        manifest = benchmark._public_manifest(
            tasks,
            _digest("failure-design"),
            _digest("failure-source"),
            _digest("failure-config"),
        )
        attempt_count = 0

        def failing_provider(prompt_text: str) -> dict[str, object]:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 5:
                raise TimeoutError("synthetic transport failure")
            return {"candidate_format": "invalid_json", "expression": None}

        with self.assertRaisesRegex(TimeoutError, "transport"):
            benchmark.collect_blind_route_response_artifact(
                manifest,
                route_id="deepseek-flash",
                send_prompt=failing_provider,
            )
        self.assertEqual(attempt_count, 5)


class ShamSelectionTests(unittest.TestCase):
    def test_profile_schema_cannot_carry_target_or_later_endpoints(self) -> None:
        self.assertEqual(
            {field.name for field in fields(benchmark.K1SupportProfile)},
            {
                "motif_id",
                "motif_sexpr",
                "motif_canonical_hash",
                "motif_behavior_hash",
                "stratum",
                "complexity_bucket",
                "k1_mask",
            },
        )

    def test_behavior_equal_factual_alias_is_excluded_and_alias_hash_is_frozen(self) -> None:
        mask = (True, False) * 5
        factual = _profile("factual", "same-behavior", mask)
        factual_alias = _profile("factual-alias", "same-behavior", mask)
        alias_a = _profile("alias-a", "eligible-behavior", mask, canonical="a")
        alias_b = _profile("alias-b", "eligible-behavior", mask, canonical="b")
        anchor = _digest("pair-anchor")
        expected = min(
            (alias_a, alias_b),
            key=lambda row: (
                benchmark._alias_representative_sha256(anchor, row), row.motif_id
            ),
        )
        selected, audit = benchmark.select_matched_sham(
            factual.motif_id,
            (factual, factual_alias, alias_a, alias_b),
            anchor,
        )
        self.assertEqual(selected, expected)
        self.assertEqual(audit["eligible_alias_count"], 2)
        self.assertEqual(audit["eligible_behavior_group_count"], 1)
        self.assertTrue(audit["all_factual_behavior_aliases_excluded"])
        self.assertFalse(audit["target_or_K2_K3_K4_read"])


class EndpointAndResponseTests(unittest.TestCase):
    def test_joint_classifier_requires_self_digests_and_shared_identity(self) -> None:
        scores = {
            route_id: _joint_score(route_id)
            for route_id in benchmark.CANONICAL_ROUTE_IDS
        }
        self.assertEqual(
            benchmark.classify_joint_routes(scores)["joint_classification"],
            "all_routes_effect_observed",
        )
        scores["deepseek-pro"]["factual_strong_F_count"] = 0
        with self.assertRaisesRegex(benchmark.FairChoiceError, "identity"):
            benchmark.classify_joint_routes(scores)

    def test_frozen_structural_policy_order_matches_config(self) -> None:
        self.assertEqual(
            benchmark.PUBLIC_K1_POLICY_IDS,
            (
                "first-public-K1-else-first-displayed",
                "public-k1-min-node-hash",
                "public-k1-min-positive-node-hash",
                "public-k1-max-parent-novelty-node-hash",
            ),
        )

    def test_frozen_pre_model_audit_distinguishes_invalid_from_valid_failure(self) -> None:
        audit = benchmark._construction_audit(_frozen_audit_pairs())
        observed = audit["observed"]
        self.assertEqual(observed["factual_K4_frame_count"], 53)
        self.assertEqual(observed["same_frame_sham_K1_supported_count"], 50)
        self.assertEqual(
            observed["same_frame_sham_K2_failure_among_K1_supported"], 34
        )
        self.assertEqual(
            observed["same_frame_sham_K2_failure_with_K1_invalid_counted_as_miss"],
            37,
        )
        self.assertEqual(
            observed["worlds_with_all_factual_K4_frames_sham_K2_failure"], 19
        )

    def test_all_rebuilt_pre_model_facts_must_match_preregistration(self) -> None:
        audit = benchmark._construction_audit(_frozen_audit_pairs())
        report = _frozen_baseline_report()
        benchmark._assert_frozen_premodel_audits(report, audit)

        drifted = json.loads(json.dumps(report))
        drifted["B_star_factual_F_count"] = 18
        with self.assertRaisesRegex(benchmark.FairChoiceError, "preregistration"):
            benchmark._assert_frozen_premodel_audits(drifted, audit)

    def test_exact_mcnemar_and_poisson_binomial_boundaries(self) -> None:
        result = benchmark.exact_one_sided_mcnemar([True, True], [False, True])
        self.assertEqual(result["exact_one_sided_p_value"]["numerator"], 1)
        self.assertEqual(result["exact_one_sided_p_value"]["denominator"], 2)
        self.assertEqual(benchmark.poisson_binomial_tail([5, 5], 2), Fraction(1, 4))

    def test_response_contract_rejects_bare_or_extra_key_without_retry(self) -> None:
        self.assertEqual(
            benchmark._response_expression({"expression": "Q12345678"}),
            "Q12345678",
        )
        self.assertIsNone(benchmark._response_expression("Q12345678"))
        self.assertIsNone(
            benchmark._response_expression(
                {"expression": "Q12345678", "explanation": "because"}
            )
        )
        accepted = SimpleNamespace(
            candidate_format="json_expression", expression="Q12345678"
        )
        self.assertEqual(benchmark._response_expression(accepted), "Q12345678")

    def test_private_action_validation_requires_symmetric_K2_K3(self) -> None:
        option_ids = benchmark.option_ids_for_pair(_digest("validation"))
        actions = []
        for raw in range(10):
            actions.append(
                {
                    "raw_action_index": raw,
                    "option_id": option_ids[raw],
                    "semantic_action": benchmark._semantic_action(raw).to_dict(),
                    "public_features": {
                        "K1_supported": False,
                        "full_domain_positive_count": None,
                        "node_count": None,
                        "child_canonical_hash": None,
                        "child_behavior_hash": None,
                        "parent_behavior_novelty_count": None,
                        "child_behavior_is_constant": None,
                    },
                    "endpoint_flags": {
                        "K1": False,
                        "K2": False,
                        "K3": False,
                        "K4_full_pool": False,
                    },
                    "full_pool_counterfactual_bundle_sha256": None,
                }
            )
        benchmark._validate_action_rows(actions)
        actions[0]["endpoint_flags"] = {
            "K1": True,
            "K2": False,
            "K3": True,
            "K4_full_pool": False,
        }
        with self.assertRaisesRegex(benchmark.FairChoiceError, "endpoints"):
            benchmark._validate_action_rows(actions)


if __name__ == "__main__":
    unittest.main()
