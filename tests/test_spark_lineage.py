from collections import Counter
from dataclasses import replace
import unittest

from src.dsl import (
    behavior_hash,
    behavior_vector,
    canonical_hash,
    evaluate,
    validate_expr,
)
from src.spark_lineage import (
    MOTIF_STRATA,
    LineageError,
    apply_edit,
    build_motif_library,
    enumerate_reachable_children,
    motif_by_id,
    replay_edit,
    select_parent,
)
from src.spark_world import generate_spark_world


class SparkLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = generate_spark_world(1000, 202)
        cls.other_target_world = generate_spark_world(1000, 909)
        cls.parent = select_parent(cls.world)
        cls.records = enumerate_reachable_children(cls.world)
        cls.other_target_records = enumerate_reachable_children(
            cls.other_target_world
        )

    def test_motif_library_covers_all_four_frozen_strata(self):
        motifs = build_motif_library()

        self.assertTrue(motifs)
        self.assertEqual(
            {motif.stratum for motif in motifs},
            set(MOTIF_STRATA),
        )
        self.assertEqual(len(MOTIF_STRATA), 4)
        self.assertEqual(len({motif.motif_id for motif in motifs}), len(motifs))
        self.assertEqual(
            len({motif.canonical_hash for motif in motifs}),
            len(motifs),
        )
        for motif in motifs:
            self.assertEqual(motif.canonical_hash, canonical_hash(motif.ast))
            self.assertEqual(motif.complexity_bucket, (motif.depth, motif.node_count))

    def test_parent_selection_is_independent_of_target_seed(self):
        self.assertNotEqual(self.world.target_index, self.other_target_world.target_index)
        self.assertEqual(self.world.hypotheses, self.other_target_world.hypotheses)
        self.assertEqual(self.world.x_train, self.other_target_world.x_train)
        self.assertEqual(self.parent, select_parent(self.other_target_world))
        self.assertEqual(
            canonical_hash(self.parent),
            canonical_hash(select_parent(self.other_target_world)),
        )

    def test_action_replays_exactly_and_rejects_wrong_old_subtree_hash(self):
        record = self.records[0]
        motif = motif_by_id(record.motif_id)

        self.assertEqual(
            apply_edit(record.parent_ast, motif, record.action),
            record.child_ast,
        )
        self.assertEqual(
            replay_edit(record.parent_ast, motif, record.action),
            record.child_ast,
        )
        self.assertEqual(record.action_hash, record.action.action_hash)
        self.assertEqual(
            record.parent_canonical_hash,
            canonical_hash(record.parent_ast),
        )
        self.assertEqual(
            record.child_canonical_hash,
            canonical_hash(record.child_ast),
        )
        self.assertEqual(
            record.child_behavior_hash,
            behavior_hash(record.child_ast, self.world.domain),
        )

        wrong_hash = "0" * 64
        if wrong_hash == record.action.expected_old_subtree_hash:
            wrong_hash = "f" * 64
        tampered = replace(
            record.action,
            expected_old_subtree_hash=wrong_hash,
        )
        with self.assertRaises(LineageError):
            apply_edit(record.parent_ast, motif, tampered)

    def test_seed_1000_has_control_ready_reachable_set(self):
        self.assertTrue(self.records)

        unique_child_behaviors = {
            behavior_vector(record.child_ast, self.world.domain)
            for record in self.records
        }
        self.assertGreaterEqual(len(unique_child_behaviors), 16)

        counts = Counter(record.motif_stratum for record in self.records)
        self.assertEqual(set(counts), set(MOTIF_STRATA))
        for stratum in MOTIF_STRATA:
            with self.subTest(stratum=stratum):
                self.assertGreaterEqual(counts[stratum], 4)

        for record in self.records:
            with self.subTest(lineage_hash=record.lineage_hash):
                self.assertGreaterEqual(len(record.matched_replacements), 2)
                self.assertEqual(
                    len(set(record.matched_replacement_motif_ids)),
                    len(record.matched_replacements),
                )
                for replacement in record.matched_replacements:
                    self.assertEqual(
                        replacement.motif_stratum,
                        record.motif_stratum,
                    )
                    self.assertNotEqual(replacement.motif_id, record.motif_id)
                    self.assertNotEqual(
                        replacement.child_behavior_hash,
                        record.child_behavior_hash,
                    )

                    replacement_motif = motif_by_id(replacement.motif_id)
                    replacement_action = replace(
                        record.action,
                        motif_id=replacement.motif_id,
                    )
                    self.assertEqual(
                        apply_edit(
                            record.parent_ast,
                            replacement_motif,
                            replacement_action,
                        ),
                        replacement.child_ast,
                    )
                    self.assertEqual(
                        replacement.child_canonical_hash,
                        canonical_hash(replacement.child_ast),
                    )
                    self.assertEqual(
                        replacement.child_behavior_hash,
                        behavior_hash(replacement.child_ast, self.world.domain),
                    )

    def test_children_are_binary_d0_consistent_and_behaviorally_novel(self):
        parent_full = behavior_vector(self.parent, self.world.domain)
        parent_evidence = tuple(
            evaluate(self.parent, point) for point in self.world.x_evidence
        )

        for record in self.records:
            with self.subTest(lineage_hash=record.lineage_hash):
                self.assertIsNone(validate_expr(record.child_ast))

                child_full = behavior_vector(record.child_ast, self.world.domain)
                self.assertLessEqual(set(child_full), {0, 1})
                self.assertNotEqual(child_full, parent_full)
                self.assertNotEqual(
                    tuple(
                        evaluate(record.child_ast, point)
                        for point in self.world.x_evidence
                    ),
                    parent_evidence,
                )
                self.assertEqual(
                    tuple(
                        evaluate(record.child_ast, point)
                        for point in self.world.x_train
                    ),
                    self.world.y_train,
                )

    def test_enumerated_lineages_are_independent_of_target_seed(self):
        self.assertEqual(
            tuple(record.lineage_hash for record in self.records),
            tuple(record.lineage_hash for record in self.other_target_records),
        )
        self.assertEqual(
            tuple(record.child_behavior_hash for record in self.records),
            tuple(
                record.child_behavior_hash
                for record in self.other_target_records
            ),
        )


if __name__ == "__main__":
    unittest.main()
