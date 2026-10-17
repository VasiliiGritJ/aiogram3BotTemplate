"""Pure, deterministic workout progression rules.

The module intentionally has no database or Telegram dependencies.  A later
read-only service will adapt durable workout snapshots to these value objects.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum


WEIGHT_QUANTUM_KG = Decimal("0.5")
WEIGHT_STEP_RATIO = Decimal("0.05")
STRENGTH_WEIGHT_STEP_RATIO = Decimal("0.025")
MAX_AUTOMATIC_WEIGHT_CHANGE_RATIO = Decimal("0.10")


class ProgressionStrategy(str, Enum):
    """Persisted protocol selected for one exercise prescription."""

    HYPERTROPHY_LOAD_REPS = "hypertrophy_load_reps"
    STRENGTH_LOAD_REPS = "strength_load_reps"
    BODYWEIGHT_REPS = "bodyweight_reps"


class ProgressionReason(str, Enum):
    """Machine-readable reason for a deterministic recommendation."""

    NO_HISTORY = "no_history"
    INSUFFICIENT_DATA = "insufficient_data"
    MIXED_WEIGHTS_HOLD = "mixed_weights_hold"
    INCREASE_WEIGHT = "increase_weight"
    HOLD_ADD_REPS = "hold_add_reps"
    HOLD_RECOVER_RANGE = "hold_recover_range"
    DECREASE_WEIGHT = "decrease_weight"
    HOLD_NO_SAFE_WEIGHT_STEP = "hold_no_safe_weight_step"
    BODYWEIGHT_HOLD_AT_UPPER = "bodyweight_hold_at_upper"
    BODYWEIGHT_ADD_REPS = "bodyweight_add_reps"
    BODYWEIGHT_RECOVER_RANGE = "bodyweight_recover_range"
    BODYWEIGHT_ADVANCE_VARIATION = "bodyweight_advance_variation"
    STRENGTH_INCREASE_WEIGHT = "strength_increase_weight"
    STRENGTH_HOLD_ADD_REPS = "strength_hold_add_reps"
    STRENGTH_HOLD_RECOVER_RANGE = "strength_hold_recover_range"
    STRENGTH_DELOAD = "strength_deload"
    STRENGTH_HOLD_NO_SAFE_WEIGHT_STEP = "strength_hold_no_safe_weight_step"


@dataclass(frozen=True)
class ProgressionTarget:
    """Structured prescription for one exercise snapshot."""

    target_sets: int
    reps_min: int
    reps_max: int


@dataclass(frozen=True)
class SetPerformance:
    """One factually recorded working set from a completed workout."""

    set_number: int
    actual_weight_kg: Decimal
    actual_reps: int


@dataclass(frozen=True)
class PreviousExercisePerformance:
    """A previous completed selected-exercise snapshot and all of its sets."""

    target: ProgressionTarget
    set_results: tuple[SetPerformance, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "set_results", tuple(self.set_results))


@dataclass(frozen=True)
class ProgressionRecommendation:
    """Advisory next-performance recommendation; it never mutates history."""

    reason: ProgressionReason
    suggested_weight_kg: Decimal | None
    suggested_reps: tuple[int, ...] | None
    previous_weights_kg: tuple[Decimal, ...] = ()
    previous_reps: tuple[int, ...] = ()
    strategy: ProgressionStrategy = ProgressionStrategy.HYPERTROPHY_LOAD_REPS
    suggested_exercise_code: str | None = None


def calculate_weight_step(
    weight_kg: Decimal,
    ratio: Decimal = WEIGHT_STEP_RATIO,
) -> Decimal | None:
    """Return the conservative generic load step or ``None`` when unsafe.

    This is an equipment-agnostic technical MVP policy, not a claim about a
    physiologically optimal increase.  The five-percent estimate is rounded
    down to the available generic 0.5 kg quantum and never exceeds ten percent
    of the prior load.
    """
    if not _is_valid_weight(weight_kg) or weight_kg <= 0:
        return None

    rounded_relative_step = (
        (weight_kg * ratio / WEIGHT_QUANTUM_KG)
        .to_integral_value(rounding=ROUND_FLOOR)
        * WEIGHT_QUANTUM_KG
    )
    step = max(WEIGHT_QUANTUM_KG, rounded_relative_step)
    if step / weight_kg > MAX_AUTOMATIC_WEIGHT_CHANGE_RATIO:
        return None
    return step


def calculate_progression(
    target: ProgressionTarget,
    previous_performance: PreviousExercisePerformance | None,
    strategy: ProgressionStrategy = ProgressionStrategy.HYPERTROPHY_LOAD_REPS,
    *,
    bodyweight_successor_code: str | None = None,
) -> ProgressionRecommendation:
    """Calculate one deterministic next-exercise recommendation.

    Only one already-selected, fully completed exercise performance is accepted
    as input.  Choosing that performance from durable history belongs to a
    later read-only service.
    """
    _require_valid_target(target)
    if not isinstance(strategy, ProgressionStrategy):
        raise ValueError("Unsupported progression strategy.")
    if previous_performance is None:
        return _recommendation(ProgressionReason.NO_HISTORY, strategy=strategy)

    normalized = _normalize_previous_performance(target, previous_performance)
    if normalized is None:
        return _recommendation(ProgressionReason.INSUFFICIENT_DATA, strategy=strategy)
    weights, reps = normalized

    if len(set(weights)) != 1:
        return _recommendation(
            ProgressionReason.MIXED_WEIGHTS_HOLD,
            previous_weights=weights,
            previous_reps=reps,
            strategy=strategy,
        )

    weight = weights[0]
    if strategy == ProgressionStrategy.BODYWEIGHT_REPS:
        if weight != 0:
            return _recommendation(
                ProgressionReason.INSUFFICIENT_DATA,
                previous_weights=weights,
                previous_reps=reps,
                strategy=strategy,
            )
        return _bodyweight_recommendation(
            target,
            reps,
            weight,
            strategy=strategy,
            successor_code=bodyweight_successor_code,
        )
    if weight == 0:
        if strategy == ProgressionStrategy.STRENGTH_LOAD_REPS:
            return _recommendation(
                ProgressionReason.INSUFFICIENT_DATA,
                previous_weights=weights,
                previous_reps=reps,
                strategy=strategy,
            )
        # Backward compatibility for legacy snapshots without a strategy.
        return _bodyweight_recommendation(
            target,
            reps,
            weight,
            strategy=strategy,
        )
    if strategy == ProgressionStrategy.STRENGTH_LOAD_REPS:
        return _strength_recommendation(target, weights, reps, weight)

    if min(reps) >= target.reps_max:
        return _weight_change_recommendation(
            target,
            weight,
            reps,
            increase=True,
        )
    if min(reps) >= target.reps_min:
        return _recommendation(
            ProgressionReason.HOLD_ADD_REPS,
            suggested_weight=weight,
            suggested_reps=tuple(min(target.reps_max, value + 1) for value in reps),
            previous_weights=weights,
            previous_reps=reps,
        )
    if max(reps) < target.reps_min:
        return _weight_change_recommendation(
            target,
            weight,
            reps,
            increase=False,
        )
    return _recommendation(
        ProgressionReason.HOLD_RECOVER_RANGE,
        suggested_weight=weight,
        suggested_reps=tuple(_clamp_reps(value, target) for value in reps),
        previous_weights=weights,
        previous_reps=reps,
    )


def _bodyweight_recommendation(
    target: ProgressionTarget,
    reps: tuple[int, ...],
    weight: Decimal,
    *,
    strategy: ProgressionStrategy,
    successor_code: str | None = None,
) -> ProgressionRecommendation:
    if min(reps) >= target.reps_max:
        if successor_code:
            return _recommendation(
                ProgressionReason.BODYWEIGHT_ADVANCE_VARIATION,
                suggested_weight=weight,
                suggested_reps=(target.reps_min,) * target.target_sets,
                previous_weights=(weight,) * target.target_sets,
                previous_reps=reps,
                strategy=strategy,
                suggested_exercise_code=successor_code,
            )
        return _recommendation(
            ProgressionReason.BODYWEIGHT_HOLD_AT_UPPER,
            suggested_weight=weight,
            suggested_reps=(target.reps_max,) * target.target_sets,
            previous_weights=(weight,) * target.target_sets,
            previous_reps=reps,
            strategy=strategy,
        )
    if min(reps) >= target.reps_min:
        return _recommendation(
            ProgressionReason.BODYWEIGHT_ADD_REPS,
            suggested_weight=weight,
            suggested_reps=tuple(min(target.reps_max, value + 1) for value in reps),
            previous_weights=(weight,) * target.target_sets,
            previous_reps=reps,
            strategy=strategy,
        )
    return _recommendation(
        ProgressionReason.BODYWEIGHT_RECOVER_RANGE,
        suggested_weight=weight,
        suggested_reps=tuple(_clamp_reps(value, target) for value in reps),
        previous_weights=(weight,) * target.target_sets,
        previous_reps=reps,
        strategy=strategy,
    )


def _strength_recommendation(
    target: ProgressionTarget,
    weights: tuple[Decimal, ...],
    reps: tuple[int, ...],
    weight: Decimal,
) -> ProgressionRecommendation:
    strategy = ProgressionStrategy.STRENGTH_LOAD_REPS
    if min(reps) >= target.reps_max:
        step = calculate_weight_step(weight, STRENGTH_WEIGHT_STEP_RATIO)
        if step is None:
            return _recommendation(
                ProgressionReason.STRENGTH_HOLD_NO_SAFE_WEIGHT_STEP,
                suggested_weight=weight,
                suggested_reps=(target.reps_max,) * target.target_sets,
                previous_weights=weights,
                previous_reps=reps,
                strategy=strategy,
            )
        return _recommendation(
            ProgressionReason.STRENGTH_INCREASE_WEIGHT,
            suggested_weight=weight + step,
            suggested_reps=(target.reps_min,) * target.target_sets,
            previous_weights=weights,
            previous_reps=reps,
            strategy=strategy,
        )
    if min(reps) >= target.reps_min:
        return _recommendation(
            ProgressionReason.STRENGTH_HOLD_ADD_REPS,
            suggested_weight=weight,
            suggested_reps=tuple(min(target.reps_max, value + 1) for value in reps),
            previous_weights=weights,
            previous_reps=reps,
            strategy=strategy,
        )
    if max(reps) >= target.reps_min:
        return _recommendation(
            ProgressionReason.STRENGTH_HOLD_RECOVER_RANGE,
            suggested_weight=weight,
            suggested_reps=tuple(_clamp_reps(value, target) for value in reps),
            previous_weights=weights,
            previous_reps=reps,
            strategy=strategy,
        )

    step = calculate_weight_step(weight, STRENGTH_WEIGHT_STEP_RATIO)
    if step is None:
        return _recommendation(
            ProgressionReason.STRENGTH_HOLD_NO_SAFE_WEIGHT_STEP,
            suggested_weight=weight,
            suggested_reps=(target.reps_min,) * target.target_sets,
            previous_weights=weights,
            previous_reps=reps,
            strategy=strategy,
        )
    return _recommendation(
        ProgressionReason.STRENGTH_DELOAD,
        suggested_weight=weight - step,
        suggested_reps=(target.reps_min,) * target.target_sets,
        previous_weights=weights,
        previous_reps=reps,
        strategy=strategy,
    )


def _weight_change_recommendation(
    target: ProgressionTarget,
    weight: Decimal,
    reps: tuple[int, ...],
    *,
    increase: bool,
) -> ProgressionRecommendation:
    step = calculate_weight_step(weight)
    if step is None:
        return _recommendation(
            ProgressionReason.HOLD_NO_SAFE_WEIGHT_STEP,
            suggested_weight=weight,
            suggested_reps=(
                (target.reps_max,) * target.target_sets
                if increase
                else (target.reps_min,) * target.target_sets
            ),
            previous_weights=(weight,) * target.target_sets,
            previous_reps=reps,
        )
    return _recommendation(
        ProgressionReason.INCREASE_WEIGHT if increase else ProgressionReason.DECREASE_WEIGHT,
        suggested_weight=weight + step if increase else weight - step,
        suggested_reps=(target.reps_min,) * target.target_sets,
        previous_weights=(weight,) * target.target_sets,
        previous_reps=reps,
    )


def _normalize_previous_performance(
    target: ProgressionTarget,
    previous_performance: PreviousExercisePerformance,
) -> tuple[tuple[Decimal, ...], tuple[int, ...]] | None:
    if not isinstance(previous_performance, PreviousExercisePerformance):
        return None
    if not _is_valid_target(previous_performance.target):
        return None
    if previous_performance.target != target:
        return None

    results = tuple(previous_performance.set_results)
    if len(results) != target.target_sets:
        return None
    if any(not isinstance(result, SetPerformance) for result in results):
        return None
    ordered_results = tuple(sorted(results, key=lambda result: result.set_number))
    if tuple(result.set_number for result in ordered_results) != tuple(
        range(1, target.target_sets + 1)
    ):
        return None
    if any(
        not _is_valid_weight(result.actual_weight_kg)
        or not _is_valid_reps(result.actual_reps)
        for result in ordered_results
    ):
        return None
    return (
        tuple(result.actual_weight_kg for result in ordered_results),
        tuple(result.actual_reps for result in ordered_results),
    )


def _recommendation(
    reason: ProgressionReason,
    *,
    suggested_weight: Decimal | None = None,
    suggested_reps: tuple[int, ...] | None = None,
    previous_weights: tuple[Decimal, ...] = (),
    previous_reps: tuple[int, ...] = (),
    strategy: ProgressionStrategy = ProgressionStrategy.HYPERTROPHY_LOAD_REPS,
    suggested_exercise_code: str | None = None,
) -> ProgressionRecommendation:
    return ProgressionRecommendation(
        reason=reason,
        suggested_weight_kg=suggested_weight,
        suggested_reps=suggested_reps,
        previous_weights_kg=previous_weights,
        previous_reps=previous_reps,
        strategy=strategy,
        suggested_exercise_code=suggested_exercise_code,
    )


def _clamp_reps(value: int, target: ProgressionTarget) -> int:
    return max(target.reps_min, min(target.reps_max, value))


def _require_valid_target(target: ProgressionTarget) -> None:
    if not _is_valid_target(target):
        raise ValueError("Progression target must have a valid set and rep range.")


def _is_valid_target(target: object) -> bool:
    return (
        isinstance(target, ProgressionTarget)
        and _is_positive_int(target.target_sets)
        and _is_positive_int(target.reps_min)
        and _is_positive_int(target.reps_max)
        and target.reps_max >= target.reps_min
    )


def _is_valid_weight(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _is_valid_reps(value: object) -> bool:
    return _is_positive_int(value)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
