import unittest
from decimal import Decimal

from services.workout_progression import (
    ProgressionReason,
    ProgressionStrategy,
    ProgressionTarget,
    PreviousExercisePerformance,
    SetPerformance,
    calculate_progression,
    calculate_weight_step,
)


def decimal(value: str) -> Decimal:
    return Decimal(value)


class WorkoutProgressionTests(unittest.TestCase):
    def target(self, sets: int = 3, minimum: int = 8, maximum: int = 12) -> ProgressionTarget:
        return ProgressionTarget(sets, minimum, maximum)

    def performance(
        self,
        reps: tuple[int, ...],
        weights: tuple[str, ...] | None = None,
        *,
        target: ProgressionTarget | None = None,
        numbers: tuple[int, ...] | None = None,
    ) -> PreviousExercisePerformance:
        current_target = target or self.target(sets=len(reps))
        set_weights = weights or ("20",) * len(reps)
        set_numbers = numbers or tuple(range(1, len(reps) + 1))
        return PreviousExercisePerformance(
            target=current_target,
            set_results=tuple(
                SetPerformance(number, decimal(weight), reps[index])
                for index, (number, weight) in enumerate(
                    zip(set_numbers, set_weights, strict=True)
                )
            ),
        )

    def assert_recommendation(
        self,
        recommendation,
        reason: ProgressionReason,
        weight: str | None,
        reps: tuple[int, ...] | None,
    ) -> None:
        self.assertEqual(reason, recommendation.reason)
        self.assertEqual(None if weight is None else decimal(weight), recommendation.suggested_weight_kg)
        self.assertEqual(reps, recommendation.suggested_reps)

    def test_no_history_returns_neutral_recommendation(self) -> None:
        recommendation = calculate_progression(self.target(), None)

        self.assert_recommendation(
            recommendation, ProgressionReason.NO_HISTORY, None, None
        )

    def test_upper_bound_on_all_sets_increases_weight(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((12, 12, 12))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.INCREASE_WEIGHT, "21", (8, 8, 8)
        )

    def test_double_progression_adds_reps_to_every_non_upper_set(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((12, 10, 8))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.HOLD_ADD_REPS, "20", (12, 11, 9)
        )

    def test_double_progression_keeps_upper_sets_and_advances_lower_set(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((12, 12, 9))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.HOLD_ADD_REPS, "20", (12, 12, 10)
        )

    def test_partial_underperformance_recovers_range_without_reducing_weight(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((10, 8, 7))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.HOLD_RECOVER_RANGE, "20", (10, 8, 8)
        )

    def test_all_sets_below_range_decreases_weight(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((7, 7, 6))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.DECREASE_WEIGHT, "19", (8, 8, 8)
        )

    def test_other_valid_rep_ranges_use_the_same_rules(self) -> None:
        target = self.target(minimum=10, maximum=15)
        hold = calculate_progression(target, self.performance((15, 12, 10), target=target))
        increase = calculate_progression(target, self.performance((15, 15, 15), target=target))

        self.assert_recommendation(
            hold, ProgressionReason.HOLD_ADD_REPS, "20", (15, 13, 11)
        )
        self.assert_recommendation(
            increase, ProgressionReason.INCREASE_WEIGHT, "21", (10, 10, 10)
        )

    def test_weight_step_is_decimal_and_rounds_down_to_half_kilogram(self) -> None:
        self.assertEqual(decimal("0.5"), calculate_weight_step(decimal("12.5")))
        self.assertEqual(decimal("1.0"), calculate_weight_step(decimal("20")))
        self.assertEqual(decimal("5.0"), calculate_weight_step(decimal("100")))

    def test_small_weight_without_safe_half_kilogram_step_holds_load(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((12, 12, 12), weights=("4", "4", "4"))
        )

        self.assertIsNone(calculate_weight_step(decimal("4")))
        self.assert_recommendation(
            recommendation, ProgressionReason.HOLD_NO_SAFE_WEIGHT_STEP, "4", (12, 12, 12)
        )

    def test_mixed_weights_do_not_produce_a_single_weight_recommendation(self) -> None:
        recommendation = calculate_progression(
            self.target(), self.performance((12, 12, 12), weights=("20", "22.5", "20"))
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.MIXED_WEIGHTS_HOLD, None, None
        )

    def test_bodyweight_never_adds_external_load(self) -> None:
        upper = calculate_progression(
            self.target(), self.performance((12, 12, 12), weights=("0", "0", "0"))
        )
        advancing = calculate_progression(
            self.target(), self.performance((12, 10, 8), weights=("0", "0", "0"))
        )
        recovering = calculate_progression(
            self.target(), self.performance((7, 7, 6), weights=("0", "0", "0"))
        )

        self.assert_recommendation(
            upper, ProgressionReason.BODYWEIGHT_HOLD_AT_UPPER, "0", (12, 12, 12)
        )
        self.assert_recommendation(
            advancing, ProgressionReason.BODYWEIGHT_ADD_REPS, "0", (12, 11, 9)
        )
        self.assert_recommendation(
            recovering, ProgressionReason.BODYWEIGHT_RECOVER_RANGE, "0", (8, 8, 8)
        )

    def test_incomplete_or_incompatible_performance_is_neutral(self) -> None:
        incomplete = self.performance((10, 10), target=self.target())
        incompatible = self.performance(
            (10, 10, 10), target=self.target(minimum=10, maximum=15)
        )
        duplicate_numbers = self.performance((10, 10, 10), numbers=(1, 1, 2))

        for performance in (incomplete, incompatible, duplicate_numbers):
            with self.subTest(performance=performance):
                recommendation = calculate_progression(self.target(), performance)
                self.assert_recommendation(
                    recommendation, ProgressionReason.INSUFFICIENT_DATA, None, None
                )

    def test_invalid_result_values_are_insufficient_data(self) -> None:
        performance = PreviousExercisePerformance(
            target=self.target(),
            set_results=(
                SetPerformance(1, decimal("20"), 10),
                SetPerformance(2, decimal("NaN"), 10),
                SetPerformance(3, decimal("20"), 10),
            ),
        )

        recommendation = calculate_progression(self.target(), performance)

        self.assert_recommendation(
            recommendation, ProgressionReason.INSUFFICIENT_DATA, None, None
        )

    def test_equal_inputs_always_produce_equal_recommendations(self) -> None:
        target = self.target()
        previous = self.performance((12, 10, 8), weights=("12.5", "12.5", "12.5"))

        self.assertEqual(
            calculate_progression(target, previous),
            calculate_progression(target, previous),
        )

    def test_strength_progression_uses_conservative_load_step(self) -> None:
        recommendation = calculate_progression(
            self.target(minimum=3, maximum=6),
            self.performance(
                (6, 6, 6),
                weights=("100", "100", "100"),
                target=self.target(minimum=3, maximum=6),
            ),
            ProgressionStrategy.STRENGTH_LOAD_REPS,
        )

        self.assert_recommendation(
            recommendation,
            ProgressionReason.STRENGTH_INCREASE_WEIGHT,
            "102.5",
            (3, 3, 3),
        )

    def test_strength_mixed_success_holds_weight_and_adds_reps(self) -> None:
        target = self.target(minimum=3, maximum=6)
        recommendation = calculate_progression(
            target,
            self.performance((6, 5, 3), weights=("100",) * 3, target=target),
            ProgressionStrategy.STRENGTH_LOAD_REPS,
        )

        self.assert_recommendation(
            recommendation,
            ProgressionReason.STRENGTH_HOLD_ADD_REPS,
            "100",
            (6, 6, 4),
        )

    def test_strength_isolated_miss_holds_but_systemic_miss_deloads(self) -> None:
        target = self.target(minimum=3, maximum=6)
        isolated = calculate_progression(
            target,
            self.performance((5, 3, 2), weights=("100",) * 3, target=target),
            ProgressionStrategy.STRENGTH_LOAD_REPS,
        )
        systemic = calculate_progression(
            target,
            self.performance((2, 2, 1), weights=("100",) * 3, target=target),
            ProgressionStrategy.STRENGTH_LOAD_REPS,
        )

        self.assert_recommendation(
            isolated,
            ProgressionReason.STRENGTH_HOLD_RECOVER_RANGE,
            "100",
            (5, 3, 3),
        )
        self.assert_recommendation(
            systemic,
            ProgressionReason.STRENGTH_DELOAD,
            "97.5",
            (3, 3, 3),
        )

    def test_bodyweight_successor_is_explicit_and_never_invents_load(self) -> None:
        upper = self.performance((12, 12, 12), weights=("0",) * 3)
        advancing = calculate_progression(
            self.target(),
            upper,
            ProgressionStrategy.BODYWEIGHT_REPS,
            bodyweight_successor_code="push_up",
        )
        holding = calculate_progression(
            self.target(),
            upper,
            ProgressionStrategy.BODYWEIGHT_REPS,
        )

        self.assert_recommendation(
            advancing,
            ProgressionReason.BODYWEIGHT_ADVANCE_VARIATION,
            "0",
            (8, 8, 8),
        )
        self.assertEqual("push_up", advancing.suggested_exercise_code)
        self.assert_recommendation(
            holding,
            ProgressionReason.BODYWEIGHT_HOLD_AT_UPPER,
            "0",
            (12, 12, 12),
        )
        self.assertIsNone(holding.suggested_exercise_code)

    def test_bodyweight_strategy_rejects_external_weight(self) -> None:
        recommendation = calculate_progression(
            self.target(),
            self.performance((12, 12, 12)),
            ProgressionStrategy.BODYWEIGHT_REPS,
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.INSUFFICIENT_DATA, None, None
        )

    def test_strength_strategy_rejects_bodyweight_history(self) -> None:
        recommendation = calculate_progression(
            self.target(minimum=3, maximum=6),
            self.performance(
                (6, 6, 6),
                weights=("0", "0", "0"),
                target=self.target(minimum=3, maximum=6),
            ),
            ProgressionStrategy.STRENGTH_LOAD_REPS,
        )

        self.assert_recommendation(
            recommendation, ProgressionReason.INSUFFICIENT_DATA, None, None
        )


if __name__ == "__main__":
    unittest.main()
