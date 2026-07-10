from __future__ import annotations

import unittest

from focusledger.services.attention import (
    ACTIVE,
    ATTENTION_GAP,
    HARD_IDLE,
    INTERRUPTED,
    LOW_ACTIVITY,
    UNKNOWN,
    AttentionPolicy,
    classify_attention,
)


class AttentionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AttentionPolicy()

    def test_default_threshold_boundaries_match_proposal(self) -> None:
        cases = (
            (0, ACTIVE, 1.0),
            (179.9, ACTIVE, 1.0),
            (180, LOW_ACTIVITY, 0.5),
            (599.9, LOW_ACTIVITY, 0.5),
            (600, ATTENTION_GAP, 0.0),
            (1199.9, ATTENTION_GAP, 0.0),
            (1200, INTERRUPTED, 0.0),
            (1799.9, INTERRUPTED, 0.0),
            (1800, HARD_IDLE, 0.0),
        )
        for idle_seconds, expected_state, expected_weight in cases:
            with self.subTest(idle_seconds=idle_seconds):
                result = classify_attention(idle_seconds, self.policy)
                self.assertEqual(result.state, expected_state)
                self.assertEqual(result.weight, expected_weight)

    def test_missing_idle_signal_is_unknown(self) -> None:
        result = classify_attention(None, self.policy)
        self.assertEqual(result.state, UNKNOWN)
        self.assertEqual(result.weight, 0.0)

    def test_thresholds_must_be_strictly_increasing(self) -> None:
        invalid = AttentionPolicy(
            low_activity_after_seconds=180,
            attention_gap_after_seconds=180,
        )
        with self.assertRaises(ValueError):
            invalid.validate()

    def test_weights_must_be_between_zero_and_one(self) -> None:
        invalid = AttentionPolicy(active_weight=1.1)
        with self.assertRaises(ValueError):
            invalid.validate()


if __name__ == "__main__":
    unittest.main()
