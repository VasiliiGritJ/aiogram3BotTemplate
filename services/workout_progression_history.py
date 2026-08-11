"""Read-only progression lookup over durable workout snapshots."""

from decimal import Decimal, InvalidOperation
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSetResult,
    dbSession,
)
from services.workout_progression import (
    PreviousExercisePerformance,
    ProgressionRecommendation,
    ProgressionTarget,
    SetPerformance,
    calculate_progression,
)


class ProgressionHistoryError(RuntimeError):
    """Base error for read-only progression-history lookup."""


class ProgressionSnapshotNotFoundError(ProgressionHistoryError):
    """Raised when the requested workout exercise snapshot is absent."""


class ProgressionOwnershipError(ProgressionHistoryError):
    """Raised when a user requests another user's workout snapshot."""


def get_progression_recommendation(
    user_id: int,
    session_exercise_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> ProgressionRecommendation:
    """Return a read-only recommendation for one owned session snapshot.

    The lookup deliberately reads at most one historical exercise snapshot.
    Decision rules remain exclusively in ``calculate_progression``.
    """
    with session_factory() as session:
        current_exercise, current_workout = _require_owned_snapshot(
            session,
            user_id,
            session_exercise_id,
        )
        target = _target_from_snapshot(current_exercise)
        previous = _get_latest_completed_performance(
            session,
            user_id,
            current_workout,
            current_exercise,
        )
        return calculate_progression(target, previous)


def _require_owned_snapshot(
    session: Session,
    user_id: int,
    session_exercise_id: int,
) -> tuple[WorkoutSessionExercise, WorkoutSession]:
    row = session.execute(
        select(WorkoutSessionExercise, WorkoutSession)
        .join(WorkoutSession, WorkoutSession.id == WorkoutSessionExercise.session_id)
        .where(WorkoutSessionExercise.id == session_exercise_id)
    ).one_or_none()
    if row is None:
        raise ProgressionSnapshotNotFoundError("Workout exercise snapshot does not exist.")

    exercise, workout = row
    if workout.user_id != user_id:
        raise ProgressionOwnershipError("Workout exercise snapshot belongs to another user.")
    return exercise, workout


def _get_latest_completed_performance(
    session: Session,
    user_id: int,
    current_workout: WorkoutSession,
    current_exercise: WorkoutSessionExercise,
) -> PreviousExercisePerformance | None:
    """Load exactly one latest prior completed selected-exercise snapshot."""
    if current_exercise.selected_exercise_id is None:
        return None

    previous_exercise = session.scalars(
        select(WorkoutSessionExercise)
        .join(WorkoutSession, WorkoutSession.id == WorkoutSessionExercise.session_id)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "completed",
            WorkoutSession.finished_at < current_workout.started_at,
            WorkoutSessionExercise.selected_exercise_id
            == current_exercise.selected_exercise_id,
        )
        .order_by(
            WorkoutSession.finished_at.desc(),
            WorkoutSession.id.desc(),
            WorkoutSessionExercise.exercise_order.desc(),
            WorkoutSessionExercise.id.desc(),
        )
        .limit(1)
    ).first()
    if previous_exercise is None:
        return None

    results = session.scalars(
        select(WorkoutSetResult)
        .where(WorkoutSetResult.session_exercise_id == previous_exercise.id)
        .order_by(WorkoutSetResult.set_number)
    ).all()
    return PreviousExercisePerformance(
        target=_target_from_snapshot(previous_exercise),
        set_results=tuple(
            SetPerformance(
                set_number=result.set_number,
                actual_weight_kg=_as_decimal(result.actual_weight_kg),
                actual_reps=result.actual_reps,
            )
            for result in results
        ),
    )


def _target_from_snapshot(exercise: WorkoutSessionExercise) -> ProgressionTarget:
    return ProgressionTarget(
        target_sets=exercise.selected_target_sets,
        reps_min=exercise.selected_target_reps_min,
        reps_max=exercise.selected_target_reps_max,
    )


def _as_decimal(value: object) -> Decimal:
    """Preserve a stored factual value while safely deferring invalid data."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("NaN")
