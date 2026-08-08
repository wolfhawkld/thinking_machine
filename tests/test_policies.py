import unittest

from src.policies import (
    ADAPTIVE_INITIAL,
    AdaptiveTemperaturePolicy,
    AnnealingPolicy,
    FixedCyclePolicy,
    FixedTemperaturePolicy,
    MultiTemperatureExchangePolicy,
    SCHEDULES,
    build_policy,
    schedule_for,
    temperature_for,
    temperatures_for,
)


class PolicyTests(unittest.TestCase):
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
