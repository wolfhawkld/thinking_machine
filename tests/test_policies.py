import unittest

from src.policies import (
    ADAPTIVE_CONTROLLER_E2,
    ADAPTIVE_CONTROLLER_V1,
    ADAPTIVE_INITIAL,
    AdaptiveTemperaturePolicy,
    AnnealingPolicy,
    FixedCyclePolicy,
    FixedTemperaturePolicy,
    MultiTemperatureExchangePolicy,
    SCHEDULES,
    ValidityNoveltyAdaptiveTemperaturePolicy,
    build_policy,
    schedule_for,
    temperature_for,
    temperatures_for,
)


class PolicyTests(unittest.TestCase):
    @staticmethod
    def e2_update(policy, **overrides):
        observation = {
            "round_index": 0,
            "round_best": 0.5,
            "best_score": 0.5,
            "pre_round_best_score": 0.5,
            "improved": False,
            "planned_candidate_count": 4,
            "valid_candidate_count": 4,
            "new_behavior_count": 1,
            "useful_new_behavior_count": 0,
        }
        observation.update(overrides)
        return policy.update(**observation)

    def test_static_arms_and_open_loop_schedules(self):
        self.assertEqual(schedule_for("L"), (0.2,) * 5)
        self.assertEqual(schedule_for("M"), (0.7,) * 5)
        self.assertEqual(schedule_for("H"), (1.2,) * 5)
        self.assertEqual(schedule_for("A"), SCHEDULES["A"])
        self.assertEqual(schedule_for("C"), SCHEDULES["C"])
        self.assertEqual(temperature_for("A", 0), 1.2)
        self.assertEqual(temperature_for("A", 99), 0.2)

    def test_adaptive_controller_matches_spec(self):
        policy = AdaptiveTemperaturePolicy()
        self.assertEqual(policy.temperature_for_round(0, {}), ADAPTIVE_INITIAL)
        policy.update(round_best=0.5, best_score=0.5, improved=False)
        self.assertAlmostEqual(policy.temperature_for_round(1, {}), 1.2)
        policy.update(round_best=0.8, best_score=0.8, improved=True)
        self.assertAlmostEqual(policy.temperature_for_round(2, {}), 1.0)
        policy.update(improved=True)
        policy.update(improved=True)
        policy.update(improved=True)
        self.assertAlmostEqual(policy.current, 0.4)
        policy.update(improved=True)
        self.assertAlmostEqual(policy.current, 0.2)
        policy.update(improved=True)
        self.assertAlmostEqual(policy.current, 0.2)

    def test_adaptive_reset_restores_configured_initial(self):
        policy = AdaptiveTemperaturePolicy(initial=0.9)
        policy.update(improved=False)
        self.assertNotEqual(policy.current, 0.9)
        policy.reset()
        self.assertAlmostEqual(policy.current, 0.9)
        self.assertEqual(policy.history, [])

    def test_e2_update_priority_and_scalar_history(self):
        low_validity = ValidityNoveltyAdaptiveTemperaturePolicy()
        record = self.e2_update(
            low_validity,
            valid_candidate_count=2,
            new_behavior_count=2,
            useful_new_behavior_count=1,
            round_best=0.75,
            best_score=0.75,
            improved=True,
        )
        self.assertEqual(record["decision_reason"], "low_validity")
        self.assertAlmostEqual(low_validity.current, 0.8)

        progress = ValidityNoveltyAdaptiveTemperaturePolicy()
        record = self.e2_update(
            progress,
            round_best=0.75,
            best_score=0.75,
            improved=True,
        )
        self.assertEqual(record["decision_reason"], "probe_improved")
        self.assertAlmostEqual(progress.current, 0.8)

        ceiling = ValidityNoveltyAdaptiveTemperaturePolicy()
        record = self.e2_update(
            ceiling,
            round_best=1.0,
            best_score=1.0,
            pre_round_best_score=1.0,
        )
        self.assertEqual(record["decision_reason"], "probe_ceiling")
        self.assertAlmostEqual(ceiling.current, 1.0)

        useful = ValidityNoveltyAdaptiveTemperaturePolicy()
        record = self.e2_update(useful, useful_new_behavior_count=1)
        self.assertEqual(record["decision_reason"], "useful_novelty")
        self.assertAlmostEqual(useful.current, 1.0)

        stale = ValidityNoveltyAdaptiveTemperaturePolicy()
        record = self.e2_update(stale)
        self.assertEqual(record["decision_reason"], "stale_search")
        self.assertAlmostEqual(stale.current, 1.2)

        self.assertTrue(
            all(
                type(value) in {str, int, float, bool}
                for value in record.values()
            )
        )
        with self.assertRaises(TypeError):
            self.e2_update(stale, candidate={"secret": "candidate"})

    def test_e2_threshold_boundaries_clipping_and_reset(self):
        policy = ValidityNoveltyAdaptiveTemperaturePolicy(initial=0.2)
        self.e2_update(
            policy,
            valid_candidate_count=2,
            new_behavior_count=0,
        )
        self.assertAlmostEqual(policy.current, 0.2)
        self.e2_update(
            policy,
            round_index=1,
            valid_candidate_count=3,
            new_behavior_count=1,
            useful_new_behavior_count=1,
        )
        self.assertAlmostEqual(policy.current, 0.2)
        self.e2_update(
            policy,
            round_index=2,
            valid_candidate_count=3,
            new_behavior_count=1,
            useful_new_behavior_count=0,
        )
        self.assertAlmostEqual(policy.current, 0.5)
        policy.reset()
        self.assertAlmostEqual(policy.current, 0.2)
        self.assertEqual(policy.history, [])

        high = ValidityNoveltyAdaptiveTemperaturePolicy(initial=1.2)
        self.e2_update(high)
        self.assertAlmostEqual(high.current, 1.2)

    def test_e2_rejects_inconsistent_or_nonfinite_scalar_observations(self):
        policy = ValidityNoveltyAdaptiveTemperaturePolicy()
        invalid = (
            {"valid_candidate_count": 5},
            {"new_behavior_count": 5},
            {"useful_new_behavior_count": 2},
            {"round_best": float("nan")},
            {"improved": 1},
            {"improved": True},
            {"best_score": 0.75},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises((TypeError, ValueError)):
                    self.e2_update(policy, **overrides)

    def test_mtx_exposes_four_slots_and_elite_state(self):
        policy = MultiTemperatureExchangePolicy()
        self.assertEqual(policy.temperatures_for_round(0), (0.2, 0.7, 0.7, 1.2))
        self.assertEqual(policy.temperature_for_slot(0, 3), 1.2)
        self.assertEqual(policy.temperature_for_round(0), 0.2)
        record = policy.update(elite="candidate", round_best=1.0)
        self.assertEqual(policy.elite, "candidate")
        self.assertEqual(record["round_best"], 1.0)
        policy.reset()
        self.assertIsNone(policy.elite)
        self.assertEqual(policy.history, [])

    def test_factory_returns_expected_classes(self):
        self.assertIsInstance(build_policy("L"), FixedTemperaturePolicy)
        self.assertIsInstance(build_policy("A"), AnnealingPolicy)
        self.assertIsInstance(build_policy("C"), FixedCyclePolicy)
        self.assertIsInstance(build_policy("MTX"), MultiTemperatureExchangePolicy)
        self.assertIsInstance(build_policy("E"), AdaptiveTemperaturePolicy)
        self.assertNotIsInstance(
            build_policy("E"),
            ValidityNoveltyAdaptiveTemperaturePolicy,
        )
        self.assertEqual(build_policy("E").controller_version, ADAPTIVE_CONTROLLER_V1)
        e2 = build_policy("E", controller_version=ADAPTIVE_CONTROLLER_E2)
        self.assertIsInstance(e2, ValidityNoveltyAdaptiveTemperaturePolicy)
        self.assertEqual(e2.controller_version, ADAPTIVE_CONTROLLER_E2)
        explicit_e2 = build_policy("E2")
        self.assertIsInstance(explicit_e2, ValidityNoveltyAdaptiveTemperaturePolicy)
        self.assertEqual(explicit_e2.arm_id, "E2")
        with self.assertRaises(ValueError):
            build_policy("E2", controller_version=ADAPTIVE_CONTROLLER_V1)
        self.assertEqual(temperatures_for("MTX"), ((0.2, 0.7, 0.7, 1.2),) * 5)

    def test_invalid_arm_and_round_are_rejected(self):
        with self.assertRaises(ValueError):
            build_policy("unknown")
        with self.assertRaises(ValueError):
            FixedTemperaturePolicy(-0.1)
        with self.assertRaises(ValueError):
            temperature_for("L", -1)


if __name__ == "__main__":
    unittest.main()
