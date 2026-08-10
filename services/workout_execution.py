"""Durable business logic for guided workout execution."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    UserAccess,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanExercise,
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSetResult,
    dbSession,
)
from services.access import (
    AccessStatus,
    activate_trial_once_in_session,
    as_utc_naive,
    evaluate_access,
    utc_now,
)


class WorkoutExecutionError(RuntimeError):
    """Base error for workout execution business rules."""


class WorkoutAccessDeniedError(WorkoutExecutionError):
    """Raised when a new workout cannot be started with current access."""


class WorkoutPlanRequiredError(WorkoutExecutionError):
    """Raised when a user has no assigned workout plan to start from."""


class WorkoutNotFoundError(WorkoutExecutionError):
    """Raised when the requested workout session does not exist."""


class WorkoutOwnershipError(WorkoutExecutionError):
    """Raised when a user tries to access another user's workout."""


class WorkoutStateError(WorkoutExecutionError):
    """Raised for invalid workout lifecycle transitions."""


class WorkoutStepError(WorkoutExecutionError):
    """Raised when a set result does not match the current expected step."""


class WorkoutResultConflictError(WorkoutExecutionError):
    """Raised when a duplicate set has conflicting actual values."""


@dataclass(frozen=True)
class WorkoutSetResultView:
    id: int
    set_number: int
    actual_weight_kg: float
    actual_reps: int
    completed_at: datetime


@dataclass(frozen=True)
class WorkoutSessionExerciseView:
    id: int
    exercise_order: int
    planned_exercise_id: int | None
    planned_exercise_name: str
    planned_primary_muscle_group: str
    planned_target_sets: int
    planned_target_reps_min: int
    planned_target_reps_max: int
    planned_rest_seconds: int
    planned_hint: str
    selected_exercise_id: int | None
    selected_exercise_name: str
    selected_primary_muscle_group: str
    selected_target_sets: int
    selected_target_reps_min: int
    selected_target_reps_max: int
    selected_rest_seconds: int
    selected_hint: str
    set_results: tuple[WorkoutSetResultView, ...]


@dataclass(frozen=True)
class WorkoutSessionView:
    id: int
    user_id: int
    source_plan_id: int | None
    source_plan_day_id: int | None
    day_number: int
    day_title: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime
    exercises: tuple[WorkoutSessionExerciseView, ...]


@dataclass(frozen=True)
class WorkoutStartResult:
    workout: WorkoutSessionView
    created: bool
    trial_activated: bool


@dataclass(frozen=True)
class CurrentWorkoutStep:
    kind: str
    workout_id: int
    exercise: WorkoutSessionExerciseView | None
    set_number: int | None

    @property
    def ready_to_complete(self) -> bool:
        return self.kind == "ready_to_complete"


@dataclass(frozen=True)
class RecordSetResult:
    result: WorkoutSetResultView
    created: bool
    next_step: CurrentWorkoutStep


def _event_time(now: datetime | None) -> datetime:
    return as_utc_naive(now) if now is not None else utc_now()


def _get_active_workout_in_session(
    session: Session,
    user_id: int,
) -> WorkoutSession | None:
    return session.scalar(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "in_progress",
        )
        .order_by(WorkoutSession.id)
    )


def _require_owned_workout(
    session: Session,
    user_id: int,
    workout_id: int,
) -> WorkoutSession:
    workout = session.get(WorkoutSession, workout_id)
    if workout is None:
        raise WorkoutNotFoundError("Workout session does not exist.")
    if workout.user_id != user_id:
        raise WorkoutOwnershipError("Workout session belongs to another user.")
    return workout


def _result_view(result: WorkoutSetResult) -> WorkoutSetResultView:
    return WorkoutSetResultView(
        id=result.id,
        set_number=result.set_number,
        actual_weight_kg=result.actual_weight_kg,
        actual_reps=result.actual_reps,
        completed_at=result.completed_at,
    )


def _exercise_view(
    session: Session,
    exercise: WorkoutSessionExercise,
) -> WorkoutSessionExerciseView:
    results = session.scalars(
        select(WorkoutSetResult)
        .where(WorkoutSetResult.session_exercise_id == exercise.id)
        .order_by(WorkoutSetResult.set_number)
    ).all()
    return WorkoutSessionExerciseView(
        id=exercise.id,
        exercise_order=exercise.exercise_order,
        planned_exercise_id=exercise.planned_exercise_id,
        planned_exercise_name=exercise.planned_exercise_name,
        planned_primary_muscle_group=exercise.planned_primary_muscle_group,
        planned_target_sets=exercise.planned_target_sets,
        planned_target_reps_min=exercise.planned_target_reps_min,
        planned_target_reps_max=exercise.planned_target_reps_max,
        planned_rest_seconds=exercise.planned_rest_seconds,
        planned_hint=exercise.planned_hint,
        selected_exercise_id=exercise.selected_exercise_id,
        selected_exercise_name=exercise.selected_exercise_name,
        selected_primary_muscle_group=exercise.selected_primary_muscle_group,
        selected_target_sets=exercise.selected_target_sets,
        selected_target_reps_min=exercise.selected_target_reps_min,
        selected_target_reps_max=exercise.selected_target_reps_max,
        selected_rest_seconds=exercise.selected_rest_seconds,
        selected_hint=exercise.selected_hint,
        set_results=tuple(_result_view(result) for result in results),
    )


def _load_workout_view(
    session: Session,
    workout: WorkoutSession,
) -> WorkoutSessionView:
    exercises = session.scalars(
        select(WorkoutSessionExercise)
        .where(WorkoutSessionExercise.session_id == workout.id)
        .order_by(WorkoutSessionExercise.exercise_order)
    ).all()
    return WorkoutSessionView(
        id=workout.id,
        user_id=workout.user_id,
        source_plan_id=workout.source_plan_id,
        source_plan_day_id=workout.source_plan_day_id,
        day_number=workout.day_number,
        day_title=workout.day_title,
        status=workout.status,
        started_at=workout.started_at,
        finished_at=workout.finished_at,
        updated_at=workout.updated_at,
        exercises=tuple(_exercise_view(session, exercise) for exercise in exercises),
    )


def _next_step_models(
    session: Session,
    workout: WorkoutSession,
) -> tuple[WorkoutSessionExercise | None, int | None]:
    exercises = session.scalars(
        select(WorkoutSessionExercise)
        .where(WorkoutSessionExercise.session_id == workout.id)
        .order_by(WorkoutSessionExercise.exercise_order)
    ).all()
    for exercise in exercises:
        completed_sets = set(
            session.scalars(
                select(WorkoutSetResult.set_number).where(
                    WorkoutSetResult.session_exercise_id == exercise.id
                )
            ).all()
        )
        for set_number in range(1, exercise.selected_target_sets + 1):
            if set_number not in completed_sets:
                return exercise, set_number
    return None, None


def _current_step_in_session(
    session: Session,
    workout: WorkoutSession,
) -> CurrentWorkoutStep:
    if workout.status != "in_progress":
        raise WorkoutStateError("Workout session is not in progress.")
    exercise, set_number = _next_step_models(session, workout)
    if exercise is None:
        return CurrentWorkoutStep(
            kind="ready_to_complete",
            workout_id=workout.id,
            exercise=None,
            set_number=None,
        )
    return CurrentWorkoutStep(
        kind="record_set",
        workout_id=workout.id,
        exercise=_exercise_view(session, exercise),
        set_number=set_number,
    )


def _selected_plan_day(
    session: Session,
    user_id: int,
    plan: UserWorkoutPlan,
) -> tuple[UserWorkoutPlanDay, list[UserWorkoutPlanExercise]]:
    days = session.scalars(
        select(UserWorkoutPlanDay)
        .where(UserWorkoutPlanDay.plan_id == plan.id)
        .order_by(UserWorkoutPlanDay.day_number)
    ).all()
    if not days:
        raise WorkoutPlanRequiredError("Assigned workout plan has no days.")

    last_completed_day = session.scalar(
        select(WorkoutSession.day_number)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.source_plan_id == plan.id,
            WorkoutSession.status == "completed",
        )
        .order_by(WorkoutSession.finished_at.desc(), WorkoutSession.id.desc())
        .limit(1)
    )
    if last_completed_day is None:
        selected_day = days[0]
    else:
        selected_day = next(
            (day for day in days if day.day_number > last_completed_day),
            days[0],
        )

    exercises = session.scalars(
        select(UserWorkoutPlanExercise)
        .where(UserWorkoutPlanExercise.plan_day_id == selected_day.id)
        .order_by(UserWorkoutPlanExercise.exercise_order)
    ).all()
    if not exercises:
        raise WorkoutPlanRequiredError("Assigned workout day has no exercises.")
    return selected_day, exercises


def _create_workout_session(
    session: Session,
    user_id: int,
    plan: UserWorkoutPlan,
    plan_day: UserWorkoutPlanDay,
    plan_exercises: list[UserWorkoutPlanExercise],
    started_at: datetime,
) -> WorkoutSession:
    workout = WorkoutSession(
        user_id=user_id,
        source_plan_id=plan.id,
        source_plan_day_id=plan_day.id,
        day_number=plan_day.day_number,
        day_title=plan_day.title,
        status="in_progress",
        started_at=started_at,
        finished_at=None,
        updated_at=started_at,
    )
    session.add(workout)
    session.flush()
    for plan_exercise in plan_exercises:
        session.add(
            WorkoutSessionExercise(
                session_id=workout.id,
                source_plan_exercise_id=plan_exercise.id,
                exercise_order=plan_exercise.exercise_order,
                planned_exercise_id=plan_exercise.exercise_id,
                planned_exercise_name=plan_exercise.exercise_name,
                planned_primary_muscle_group=plan_exercise.primary_muscle_group,
                planned_target_sets=plan_exercise.sets,
                planned_target_reps_min=plan_exercise.reps_min,
                planned_target_reps_max=plan_exercise.reps_max,
                planned_rest_seconds=plan_exercise.rest_seconds,
                planned_hint=plan_exercise.hint,
                selected_exercise_id=plan_exercise.exercise_id,
                selected_exercise_name=plan_exercise.exercise_name,
                selected_primary_muscle_group=plan_exercise.primary_muscle_group,
                selected_target_sets=plan_exercise.sets,
                selected_target_reps_min=plan_exercise.reps_min,
                selected_target_reps_max=plan_exercise.reps_max,
                selected_rest_seconds=plan_exercise.rest_seconds,
                selected_hint=plan_exercise.hint,
            )
        )
    session.flush()
    return workout


def get_active_workout(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutSessionView | None:
    """Return the durable active workout without checking whether access expired."""
    with session_factory() as session:
        workout = _get_active_workout_in_session(session, user_id)
        return None if workout is None else _load_workout_view(session, workout)


def get_or_start_workout(
    user_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutStartResult:
    """Resume one active workout or atomically start a new authorized workout."""
    started_at = _event_time(now)
    try:
        with session_factory() as session:
            with session.begin():
                existing = _get_active_workout_in_session(session, user_id)
                if existing is not None:
                    return WorkoutStartResult(
                        workout=_load_workout_view(session, existing),
                        created=False,
                        trial_activated=False,
                    )

                plan = session.scalar(
                    select(UserWorkoutPlan).where(
                        UserWorkoutPlan.user_id == user_id
                    )
                )
                if plan is None:
                    raise WorkoutPlanRequiredError(
                        "Assign a workout plan before starting a workout."
                    )
                plan_day, plan_exercises = _selected_plan_day(session, user_id, plan)

                access = session.get(UserAccess, user_id)
                decision = evaluate_access(access, started_at)
                trial_activated = False
                if decision.status == AccessStatus.TRIAL_AVAILABLE:
                    activation = activate_trial_once_in_session(
                        user_id,
                        session,
                        started_at,
                    )
                    trial_activated = activation.activated
                elif not decision.has_access:
                    raise WorkoutAccessDeniedError(
                        "Current access does not allow a new workout."
                    )

                workout = _create_workout_session(
                    session,
                    user_id,
                    plan,
                    plan_day,
                    plan_exercises,
                    started_at,
                )
                return WorkoutStartResult(
                    workout=_load_workout_view(session, workout),
                    created=True,
                    trial_activated=trial_activated,
                )
    except IntegrityError:
        # A competing transaction may have inserted the one allowed active
        # session after this call performed its initial lookup.
        with session_factory() as session:
            existing = _get_active_workout_in_session(session, user_id)
            if existing is not None:
                return WorkoutStartResult(
                    workout=_load_workout_view(session, existing),
                    created=False,
                    trial_activated=False,
                )
        raise


def get_current_step(
    user_id: int,
    session_id: int | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> CurrentWorkoutStep:
    """Calculate the next required set from persisted snapshots and results."""
    with session_factory() as session:
        workout = (
            _require_owned_workout(session, user_id, session_id)
            if session_id is not None
            else _get_active_workout_in_session(session, user_id)
        )
        if workout is None:
            raise WorkoutNotFoundError("No active workout session exists.")
        return _current_step_in_session(session, workout)


def _validate_result_values(actual_weight_kg: float, actual_reps: int) -> None:
    if (
        isinstance(actual_weight_kg, bool)
        or not isinstance(actual_weight_kg, (int, float))
        or not math.isfinite(actual_weight_kg)
        or actual_weight_kg < 0
    ):
        raise WorkoutStepError("Actual weight must be a finite non-negative number.")
    if (
        isinstance(actual_reps, bool)
        or not isinstance(actual_reps, int)
        or actual_reps < 1
    ):
        raise WorkoutStepError("Actual repetitions must be a positive integer.")


def record_set_result(
    user_id: int,
    session_id: int,
    session_exercise_id: int,
    set_number: int,
    actual_weight_kg: float,
    actual_reps: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> RecordSetResult:
    """Persist exactly the expected set without silently overwriting history."""
    completed_at = _event_time(now)
    _validate_result_values(actual_weight_kg, actual_reps)
    with session_factory() as session:
        with session.begin():
            workout = _require_owned_workout(session, user_id, session_id)
            if workout.status != "in_progress":
                raise WorkoutStateError("Workout session is not in progress.")
            exercise = session.get(WorkoutSessionExercise, session_exercise_id)
            if exercise is None or exercise.session_id != workout.id:
                raise WorkoutStepError("Workout exercise does not belong to this session.")

            existing = session.scalar(
                select(WorkoutSetResult).where(
                    WorkoutSetResult.session_exercise_id == session_exercise_id,
                    WorkoutSetResult.set_number == set_number,
                )
            )
            if existing is not None:
                if (
                    existing.actual_weight_kg == actual_weight_kg
                    and existing.actual_reps == actual_reps
                ):
                    return RecordSetResult(
                        result=_result_view(existing),
                        created=False,
                        next_step=_current_step_in_session(session, workout),
                    )
                raise WorkoutResultConflictError(
                    "A different result is already saved for this set."
                )

            expected_exercise, expected_set = _next_step_models(session, workout)
            if (
                expected_exercise is None
                or expected_exercise.id != session_exercise_id
                or expected_set != set_number
            ):
                raise WorkoutStepError("Set result is not the current expected step.")
            result = WorkoutSetResult(
                session_exercise_id=session_exercise_id,
                set_number=set_number,
                actual_weight_kg=float(actual_weight_kg),
                actual_reps=actual_reps,
                completed_at=completed_at,
            )
            session.add(result)
            session.flush()
            return RecordSetResult(
                result=_result_view(result),
                created=True,
                next_step=_current_step_in_session(session, workout),
            )


def complete_workout(
    user_id: int,
    session_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutSessionView:
    """Complete an in-progress workout after every selected set is saved."""
    finished_at = _event_time(now)
    with session_factory() as session:
        with session.begin():
            workout = _require_owned_workout(session, user_id, session_id)
            if workout.status == "completed":
                return _load_workout_view(session, workout)
            if workout.status != "in_progress":
                raise WorkoutStateError("Cancelled workout cannot be completed.")
            if not _current_step_in_session(session, workout).ready_to_complete:
                raise WorkoutStateError("All selected workout sets must be completed first.")
            workout.status = "completed"
            workout.finished_at = finished_at
            workout.updated_at = finished_at
            session.flush()
            return _load_workout_view(session, workout)


def cancel_workout(
    user_id: int,
    session_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutSessionView:
    """Cancel an active workout while retaining any saved set results."""
    finished_at = _event_time(now)
    with session_factory() as session:
        with session.begin():
            workout = _require_owned_workout(session, user_id, session_id)
            if workout.status == "cancelled":
                return _load_workout_view(session, workout)
            if workout.status != "in_progress":
                raise WorkoutStateError("Completed workout cannot be cancelled.")
            workout.status = "cancelled"
            workout.finished_at = finished_at
            workout.updated_at = finished_at
            session.flush()
            return _load_workout_view(session, workout)


def get_workout_history(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> tuple[WorkoutSessionView, ...]:
    """Return completed workout snapshots newest first; cancelled sessions are excluded."""
    with session_factory() as session:
        workouts = session.scalars(
            select(WorkoutSession)
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == "completed",
            )
            .order_by(WorkoutSession.finished_at.desc(), WorkoutSession.id.desc())
        ).all()
        return tuple(_load_workout_view(session, workout) for workout in workouts)
