"""Synthetic contract tests for the bounded shortcut-challenge engine.

These tests deliberately replace every world/compressor seam.  They exercise
the seed contract, the one-target materialisation boundary, and the public
nonconstant diagnostic without constructing a real world or reading a model
response.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "shortcut_challenge_engine",
    Path(__file__).with_name("shortcut_challenge_engine_20260909.py"),
)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import guard
    raise RuntimeError("unable to load shortcut-challenge engine")
ENGINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENGINE)


def _action(raw: int, novelty: int, nodes: int, *, constant: bool = False) -> dict:
    """Return one public action row with a deterministic synthetic hash."""

    digest = hashlib.sha256(f"synthetic-child-{raw}".encode("ascii")).hexdigest()
    return {
        "raw_action_index": raw,
        "public_features": {
            "K1_supported": True,
            "child_behavior_is_constant": constant,
            "parent_behavior_novelty_count": novelty,
            "node_count": nodes,
            "child_canonical_hash": digest,
            "full_domain_positive_count": raw,
        },
    }


def _profile(motif_id: str, behavior: str, *, correct: int) -> dict:
    """Minimal profile accepted by the synthetic pair seam."""

    actions = []
    for raw in range(10):
        actions.append(
            {
                "raw_action_index": raw,
                "endpoint_flags": {
                    "K1": True,
                    "K2": True,
                    "K3": True,
                    "K4_full_pool": raw == correct,
                },
                "child_behavior_hash": hashlib.sha256(
                    f"{motif_id}-{raw}".encode("ascii")
                ).hexdigest(),
                "child_behavior_is_constant": False,
                "child_canonical_hash": hashlib.sha256(
                    f"child-{motif_id}-{raw}".encode("ascii")
                ).hexdigest(),
            }
        )
    return {
        "motif_id": motif_id,
        "motif_sexpr": f"(const {motif_id})",
        "motif_canonical_hash": hashlib.sha256(
            f"canonical-{motif_id}".encode("ascii")
        ).hexdigest(),
        "motif_behavior_hash": behavior,
        "stratum": "affine_commutative",
        "complexity_bucket": [2, 3],
        "raw_action_count": 10,
        "k1_raw_action_indices": list(range(10)),
        "k2_raw_action_indices": [0, 1],
        "k3_raw_action_indices": list(range(10)),
        "k4_raw_action_indices": [correct],
        "nonconstant_k4_raw_action_indices": [correct],
        "constant_k4_raw_action_indices": [],
        "actions": actions,
    }


class _FakeExample:
    def __init__(self, point: tuple[int, int, int], label: int) -> None:
        self.point = point
        self.label = label


class _FakeWorld:
    world_hash = "f" * 64
    target_index = 1
    domain = tuple((x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1))
    train = (_FakeExample((-1, -1, -1), 0),)
    evidence = (_FakeExample((0, 0, 0), 0),)
    test = (_FakeExample((1, 1, 1), 1),)

    def __init__(self, seed: int, target_seed: int) -> None:
        self.world_seed = seed
        self.target_seed = target_seed
        parent = ("ite", ("gt", ("var", "x1"), ("const", 0)), ("const", 1), ("const", 0))
        target = ("ite", ("gt", ("var", "x2"), ("const", 0)), ("const", 1), ("const", 0))
        self.hypotheses = (parent, target)

    @property
    def target(self):
        return self.hypotheses[self.target_index]


class _FakeCompressor:
    domain = _FakeWorld.domain

    def __init__(self, world) -> None:
        self.world = world
        self.calls: list[tuple[object, int]] = []

    def run(self, ast, *, max_rounds: int):
        self.calls.append((ast, max_rounds))
        return SimpleNamespace()


class _FakeCachedCompressor(_FakeCompressor):
    cache_hits = 0
    cache_misses = 0


class EngineContractTests(unittest.TestCase):
    def test_diagnostic_order_all_128_and_old_prefix_equivalence(self):
        namespace = ENGINE.NAMESPACE + ':diagnostic-display'
        for index in range(128):
            order = ENGINE.b.action_order_for_pair(index % 10, namespace)
            self.assertEqual(sorted(order), list(range(10)))
            if index < 24:
                self.assertEqual(order, ENGINE.b.action_order_for_pair(index, namespace))

    def test_seed_vector_is_fixed_128_and_digest_bound(self) -> None:
        seeds = ENGINE.seed_vector()
        self.assertIsInstance(seeds, list)
        self.assertEqual(len(seeds), 128)
        self.assertEqual(len(set(seeds)), 128)
        self.assertTrue(all(type(seed) is int and 0 <= seed < (1 << 63) for seed in seeds))
        digest = hashlib.sha256(json.dumps(seeds, separators=(",", ":")).encode("ascii")).hexdigest()
        self.assertEqual(
            digest,
            "e57785d0b2580a2918e965bc13f35010a32603dba1c8690d61111b76bb9f3655",
        )

    def test_seed_collision_census_is_offline_and_zero(self) -> None:
        with patch.object(ENGINE, "generate_spark_world") as generated:
            result = ENGINE.check_seed_collisions()
        generated.assert_not_called()
        self.assertEqual(result["unique"], 128)
        self.assertEqual(result["collisions"], 0)
        self.assertEqual(result["historical_union_count"], 4204)

    def test_scan_materializes_exactly_one_target_with_new_namespace(self) -> None:
        seed = ENGINE.seed_vector()[0]
        world = _FakeWorld(seed, 0)
        motifs = tuple({"motif_id": f"m{i}"} for i in range(105))
        profile_map = {
            motif["motif_id"]: _profile(
                motif["motif_id"], hashlib.sha256(motif["motif_id"].encode("ascii")).hexdigest(),
                correct=(0 if motif["motif_id"] == "m0" else 1),
            )
            for motif in motifs
        }
        strict_pair = {
            "tier_id": "strict_unique_nonconstant_switch",
            "stratum": "affine_commutative",
            "complexity_bucket": [2, 3],
            "context_a_motif_id": "m0",
            "context_b_motif_id": "m1",
            "context_a_correct_raw_action_indices": [0],
            "context_b_correct_raw_action_indices": [1],
            "correct_raw_action_sets_disjoint": True,
            "k2_opportunity_count": 2,
        }
        fake_public = {
            motif["motif_id"]: [_action(raw, 10 - raw, raw + 1) for raw in range(10)]
            for motif in motifs
        }

        def fake_profile(_world, motif, *_args):
            return profile_map[motif["motif_id"]]

        with patch.object(ENGINE, "generate_spark_world", return_value=world) as generated, \
            patch.object(ENGINE.f, "enumerate_full_motif_library", return_value=motifs), \
            patch.object(ENGINE, "SparkCompressor", side_effect=_FakeCompressor), \
            patch.object(ENGINE.f, "_BehaviorCachedCompressor", side_effect=_FakeCachedCompressor), \
            patch.object(ENGINE.spark_lineage, "select_parent", return_value=world.hypotheses[0]), \
            patch.object(ENGINE.spark_lineage, "enumerate_reachable_children", return_value=()), \
            patch.object(ENGINE.f, "_lineage_index", return_value={}), \
            patch.object(ENGINE.f, "_raw_actions", return_value=tuple(object() for _ in range(10))), \
            patch.object(ENGINE.f, "_context_profile", side_effect=fake_profile), \
            patch.object(ENGINE.f, "_validate_profiles"), \
            patch.object(ENGINE.b, "_public_action_features", side_effect=lambda _w, motif_id, _l: fake_public[motif_id]), \
            patch.object(ENGINE.f, "pair_candidates_for_world", return_value={
                "strict_unique_nonconstant_switch": [strict_pair],
                "degraded_two_choice_disjoint_switch": [],
            }):
            result = ENGINE.scan_world(0, seed)

        target_seed = ENGINE.f.derive_private_target_seed(
            {"development_target_materialization": {"target_seed_namespace": ENGINE.NAMESPACE}},
            seed,
        )
        generated.assert_called_once_with(seed, target_seed=target_seed)
        self.assertEqual(result["candidate_index"], 0)
        self.assertEqual(result["world_seed"], seed)
        self.assertEqual(len(result["profiles"]), 105)
        self.assertIn("strict_unique_nonconstant_switch", result["pair_candidates"])
        self.assertIn("task_identity", result)
        self.assertIn("prompt_identity", result)
        json.dumps(result, sort_keys=True)

    def test_arm_diagnostic_filters_constant_and_reports_decoy_tie_counters(self) -> None:
        diagnostic = ENGINE.helpers().arm_diagnostic
        actions = [
            _action(0, 99, 1, constant=True),
            _action(1, 5, 4),
            _action(2, 8, 6),
            _action(3, 5, 4),
        ]
        report = diagnostic(actions, 1)
        self.assertEqual(ENGINE.nonconstant_select(actions, list(range(10)), "novelty"), 2)
        self.assertTrue(report["policy_wrong"])
        self.assertTrue(report["strict_novelty_decoy"])
        self.assertTrue(report["novelty_node_tie"])
        self.assertNotIn(0, report.get("nonconstant_supported_raw_action_indices", ()))


if __name__ == "__main__":
    unittest.main()
