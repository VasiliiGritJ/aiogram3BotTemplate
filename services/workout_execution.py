"""Durable business logic for guided workout execution."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    Exercise,
    FitnessProfile,
    UserAccess,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanBlock,
    UserWorkoutPlanExercise,
    WorkoutSession,
    WorkoutSessionBlock,
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
from services.exercise_catalog import (
    EXERCISE_DEFINITIONS,
    EXPERIENCE_LEVELS,
    TRAINING_ENVIRONMENTS,
    ExerciseDefinition,
    ExerciseTechnique,
    exercise_definition_by_code,
)
from services.workout_plans import strength_profile_constraint_message


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


class WorkoutEnvironmentError(WorkoutExecutionError):
    """Raised when a per-session training environment cannot be applied safely."""


class WorkoutEnvironmentChangeBlockedError(WorkoutEnvironmentError):
    """Raised after the first durable workout result has been recorded."""


class WorkoutEnvironmentIncompatibleError(WorkoutEnvironmentError):
    """Raised when the current plan cannot be safely used in an environment."""


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
    planned_progression_strategy: str | None = None
    selected_progression_strategy: str | None = None
    session_block_id: int | None = None
    planned_format_reps: int | None = None
    selected_format_reps: int | None = None
    planned_station_order: int | None = None
    selected_station_order: int | None = None
    # Catalog code is read-only metadata used for deterministic UI preparation.
    # The selected exercise ID and the stored snapshot remain the source of truth.
    selected_exercise_code: str | None = None


@dataclass(frozen=True)
class WorkoutSessionBlockView:
    id: int
    block_order: int
    title: str
    workout_format: str
    duration_seconds: int | None
    target_rounds: int | None
    started_at: datetime | None
    finished_at: datetime | None
    completed_rounds: int
    partial_station_order: int | None
    partial_reps: int
    completed_minutes: int
    missed_minutes: int
    elapsed_seconds: int | None
    final_score: int | None
    exercises: tuple[WorkoutSessionExerciseView, ...]


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
    blocks: tuple[WorkoutSessionBlockView, ...] = ()
    plan_source: str = "generated"
    adaptation_mode: str = "adaptive"
    effective_training_environment: str | None = None


@dataclass(frozen=True)
class WorkoutStartResult:
    workout: WorkoutSessionView
    created: bool
    trial_activated: bool


@dataclass(frozen=True)
class WorkoutHistoryPage:
    """One bounded page of completed workout snapshots."""

    workouts: tuple[WorkoutSessionView, ...]
    offset: int
    page_size: int
    has_newer: bool
    has_older: bool


@dataclass(frozen=True)
class CurrentWorkoutStep:
    kind: str
    workout_id: int
    exercise: WorkoutSessionExerciseView | None
    set_number: int | None
    format_block: WorkoutSessionBlockView | None = None

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
    selected_code = (
        None
        if exercise.selected_exercise_id is None
        else session.scalar(
            select(Exercise.code).where(Exercise.id == exercise.selected_exercise_id)
        )
    )
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
        planned_progression_strategy=exercise.planned_progression_strategy,
        selected_progression_strategy=exercise.selected_progression_strategy,
        session_block_id=exercise.session_block_id,
        planned_format_reps=exercise.planned_format_reps,
        selected_format_reps=exercise.selected_format_reps,
        planned_station_order=exercise.planned_station_order,
        selected_station_order=exercise.selected_station_order,
        selected_exercise_code=selected_code,
    )


def _block_view(session: Session, block: WorkoutSessionBlock) -> WorkoutSessionBlockView:
    exercises = session.scalars(
        select(WorkoutSessionExercise)
        .where(WorkoutSessionExercise.session_block_id == block.id)
        .order_by(WorkoutSessionExercise.selected_station_order, WorkoutSessionExercise.exercise_order)
    ).all()
    return WorkoutSessionBlockView(
        id=block.id, block_order=block.block_order, title=block.title,
        workout_format=block.workout_format, duration_seconds=block.duration_seconds,
        target_rounds=block.target_rounds, started_at=block.started_at,
        finished_at=block.finished_at, completed_rounds=block.completed_rounds,
        partial_station_order=block.partial_station_order, partial_reps=block.partial_reps,
        completed_minutes=block.completed_minutes, missed_minutes=block.missed_minutes,
        elapsed_seconds=block.elapsed_seconds, final_score=block.final_score,
        exercises=tuple(_exercise_view(session, item) for item in exercises),
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
    blocks = session.scalars(
        select(WorkoutSessionBlock)
        .where(WorkoutSessionBlock.session_id == workout.id)
        .order_by(WorkoutSessionBlock.block_order)
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
        blocks=tuple(_block_view(session, block) for block in blocks),
        plan_source=workout.plan_source,
        adaptation_mode=workout.adaptation_mode,
        effective_training_environment=workout.effective_training_environment,
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
        if exercise.session_block_id is not None:
            block = session.get(WorkoutSessionBlock, exercise.session_block_id)
            if block is not None and block.workout_format != "standard_sets":
                continue
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
        block = session.scalar(
            select(WorkoutSessionBlock)
            .where(
                WorkoutSessionBlock.session_id == workout.id,
                WorkoutSessionBlock.workout_format != "standard_sets",
                WorkoutSessionBlock.finished_at.is_(None),
            )
            .order_by(WorkoutSessionBlock.block_order)
        )
        if block is not None:
            return CurrentWorkoutStep(
                kind="format_block", workout_id=workout.id, exercise=None,
                set_number=None, format_block=_block_view(session, block),
            )
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


_DEFINITIONS_BY_CODE = {item.code: item for item in EXERCISE_DEFINITIONS}


def _controlled_exercise_definition(
    session: Session,
    exercise_id: int | None,
) -> tuple[ExerciseDefinition, Exercise] | None:
    if exercise_id is None:
        return None
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return None
    definition = _DEFINITIONS_BY_CODE.get(exercise.code)
    return None if definition is None else (definition, exercise)


def _environment_candidate(
    session: Session,
    exercise_id: int | None,
    *,
    training_environment: str,
    experience_level: str,
    used_exercise_ids: set[int],
    strict: bool,
) -> Exercise:
    source = _controlled_exercise_definition(session, exercise_id)
    if source is None:
        source_model = None if exercise_id is None else session.get(Exercise, exercise_id)
        if source_model is None:
            raise WorkoutEnvironmentIncompatibleError(
                "Exercise has no controlled environment metadata."
            )
        stored_environments = {
            value.strip()
            for value in (source_model.training_environments or "").split(",")
            if value.strip()
        }
        stored_levels = {
            value.strip()
            for value in (source_model.experience_levels or "").split(",")
            if value.strip()
        }
        if (
            training_environment in stored_environments
            and experience_level in stored_levels
        ):
            return source_model
        raise WorkoutEnvironmentIncompatibleError(
            "Exercise has no controlled alternative for this environment."
        )
    source_definition, source_model = source
    if (
        training_environment in source_definition.environments
        and experience_level in source_definition.experience_levels
    ):
        return source_model
    if strict:
        raise WorkoutEnvironmentIncompatibleError(
            "Strict user program is incompatible with this environment."
        )

    definitions = [
        item
        for item in EXERCISE_DEFINITIONS
        if training_environment in item.environments
        and experience_level in item.experience_levels
        and item.primary_muscle_group == source_definition.primary_muscle_group
        and item.movement_pattern == source_definition.movement_pattern
        and item.progression_type in {"external_load_reps", "bodyweight_reps"}
        and not (
            training_environment == "home" and item.equipment != "bodyweight"
        )
    ]
    if not definitions and source_definition.primary_muscle_group in {"biceps", "triceps"}:
        substitute_muscle = (
            "back" if source_definition.primary_muscle_group == "biceps" else "chest"
        )
        substitute_movements = (
            {"horizontal_pull", "vertical_pull"}
            if substitute_muscle == "back"
            else {"horizontal_push", "vertical_push"}
        )
        definitions = [
            item
            for item in EXERCISE_DEFINITIONS
            if training_environment in item.environments
            and experience_level in item.experience_levels
            and item.primary_muscle_group == substitute_muscle
            and item.movement_pattern in substitute_movements
            and item.progression_type in {"external_load_reps", "bodyweight_reps"}
            and not (
                training_environment == "home" and item.equipment != "bodyweight"
            )
        ]
    if not definitions:
        raise WorkoutEnvironmentIncompatibleError(
            "No controlled alternative is available for this environment."
        )
    rows = session.scalars(
        select(Exercise).where(Exercise.code.in_([item.code for item in definitions]))
    ).all()
    by_code = {row.code: row for row in rows}
    candidates = [
        (definition, by_code[definition.code])
        for definition in definitions
        if definition.code in by_code
    ]
    if not candidates:
        raise WorkoutEnvironmentIncompatibleError(
            "Controlled alternatives are not synchronized to the database."
        )
    candidates.sort(
        key=lambda pair: (
            0
            if pair[0].equivalence_group == source_definition.equivalence_group
            else 1,
            0
            if pair[0].progression_type == source_definition.progression_type
            else 1,
            1 if pair[1].id in used_exercise_ids else 0,
            0
            if pair[0].equipment == source_definition.equipment
            else 1,
            pair[0].code,
        )
    )
    return candidates[0][1]


def _selected_environment_exercises(
    session: Session,
    plan: UserWorkoutPlan,
    plan_exercises: list[UserWorkoutPlanExercise],
    training_environment: str,
    experience_level: str,
) -> dict[int, Exercise]:
    strict = plan.plan_source == "user_defined" and plan.adaptation_mode == "strict"
    selected: dict[int, Exercise] = {}
    used_ids: set[int] = set()
    for plan_exercise in plan_exercises:
        exercise = _environment_candidate(
            session,
            plan_exercise.exercise_id,
            training_environment=training_environment,
            experience_level=experience_level,
            used_exercise_ids=used_ids,
            strict=strict,
        )
        selected[plan_exercise.id] = exercise
        used_ids.add(exercise.id)
    return selected


def _environment_progression_strategy(
    planned_strategy: str | None,
    selected: Exercise,
) -> str | None:
    definition = _DEFINITIONS_BY_CODE.get(selected.code)
    if definition is not None and definition.progression_type == "bodyweight_reps":
        return "bodyweight_reps"
    if (
        definition is not None
        and definition.progression_type == "external_load_reps"
        and planned_strategy == "bodyweight_reps"
    ):
        return "hypertrophy_load_reps"
    return planned_strategy


def _create_workout_session(
    session: Session,
    user_id: int,
    plan: UserWorkoutPlan,
    plan_day: UserWorkoutPlanDay,
    plan_exercises: list[UserWorkoutPlanExercise],
    started_at: datetime,
    effective_training_environment: str,
    experience_level: str,
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
        plan_source=plan.plan_source,
        adaptation_mode=plan.adaptation_mode,
        effective_training_environment=effective_training_environment,
    )
    session.add(workout)
    session.flush()
    block_map: dict[int, WorkoutSessionBlock] = {}
    plan_blocks = session.scalars(
        select(UserWorkoutPlanBlock)
        .where(UserWorkoutPlanBlock.plan_day_id == plan_day.id)
        .order_by(UserWorkoutPlanBlock.block_order)
    ).all()
    for plan_block in plan_blocks:
        snapshot = WorkoutSessionBlock(
            session_id=workout.id,
            source_plan_block_id=plan_block.id,
            block_order=plan_block.block_order,
            title=plan_block.title,
            workout_format=plan_block.workout_format,
            duration_seconds=plan_block.duration_seconds,
            target_rounds=plan_block.target_rounds,
            updated_at=started_at,
        )
        session.add(snapshot)
        session.flush()
        block_map[plan_block.id] = snapshot
    selected_by_plan_id = _selected_environment_exercises(
        session,
        plan,
        plan_exercises,
        effective_training_environment,
        experience_level,
    )
    for plan_exercise in plan_exercises:
        selected = selected_by_plan_id[plan_exercise.id]
        replaced = selected.id != plan_exercise.exercise_id
        session.add(
            WorkoutSessionExercise(
                session_id=workout.id,
                source_plan_exercise_id=plan_exercise.id,
                session_block_id=(
                    block_map[plan_exercise.plan_block_id].id
                    if plan_exercise.plan_block_id in block_map else None
                ),
                exercise_order=plan_exercise.exercise_order,
                planned_exercise_id=plan_exercise.exercise_id,
                planned_exercise_name=plan_exercise.exercise_name,
                planned_primary_muscle_group=plan_exercise.primary_muscle_group,
                planned_target_sets=plan_exercise.sets,
                planned_target_reps_min=plan_exercise.reps_min,
                planned_target_reps_max=plan_exercise.reps_max,
                planned_rest_seconds=plan_exercise.rest_seconds,
                planned_hint=plan_exercise.hint,
                planned_progression_strategy=plan_exercise.progression_strategy,
                planned_format_reps=plan_exercise.format_reps,
                planned_station_order=plan_exercise.station_order,
                selected_exercise_id=selected.id,
                selected_exercise_name=(
                    selected.name if replaced else plan_exercise.exercise_name
                ),
                selected_primary_muscle_group=(
                    selected.primary_muscle_group
                    if replaced else plan_exercise.primary_muscle_group
                ),
                selected_target_sets=plan_exercise.sets,
                selected_target_reps_min=plan_exercise.reps_min,
                selected_target_reps_max=plan_exercise.reps_max,
                selected_rest_seconds=plan_exercise.rest_seconds,
                selected_hint=selected.hint if replaced else plan_exercise.hint,
                selected_progression_strategy=_environment_progression_strategy(
                    plan_exercise.progression_strategy,
                    selected,
                ),
                selected_format_reps=plan_exercise.format_reps,
                selected_station_order=plan_exercise.station_order,
            )
        )
    session.flush()
    return workout


def get_default_training_environment(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> str:
    """Return the validated profile default without mutating profile or workout."""
    with session_factory() as session:
        profile = session.get(FitnessProfile, user_id)
        if profile is None or profile.training_environment not in TRAINING_ENVIRONMENTS:
            raise WorkoutEnvironmentIncompatibleError(
                "A training environment is required in the profile."
            )
        return profile.training_environment


def change_workout_environment(
    user_id: int,
    workout_id: int,
    training_environment: str,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutSessionView:
    """Adapt only unstarted selected snapshots and persist one session override."""
    if training_environment not in TRAINING_ENVIRONMENTS:
        raise WorkoutEnvironmentIncompatibleError(
            "Unsupported training environment."
        )
    with session_factory() as session:
        with session.begin():
            workout = _require_owned_workout(session, user_id, workout_id)
            if workout.status != "in_progress":
                raise WorkoutStateError("Workout session is not in progress.")
            saved_result = session.scalar(
                select(WorkoutSetResult.id)
                .join(
                    WorkoutSessionExercise,
                    WorkoutSessionExercise.id == WorkoutSetResult.session_exercise_id,
                )
                .where(WorkoutSessionExercise.session_id == workout.id)
                .limit(1)
            )
            started_block = session.scalar(
                select(WorkoutSessionBlock.id)
                .where(
                    WorkoutSessionBlock.session_id == workout.id,
                    (
                        WorkoutSessionBlock.started_at.is_not(None)
                        | WorkoutSessionBlock.finished_at.is_not(None)
                    ),
                )
                .limit(1)
            )
            if saved_result is not None or started_block is not None:
                raise WorkoutEnvironmentChangeBlockedError(
                    "Training environment cannot change after work is recorded."
                )

            profile = session.get(FitnessProfile, user_id)
            if profile is None or profile.experience_level not in EXPERIENCE_LEVELS:
                raise WorkoutEnvironmentIncompatibleError(
                    "A complete training profile is required."
                )
            constraint = strength_profile_constraint_message(
                goal=profile.goal,
                experience_level=profile.experience_level,
                training_environment=training_environment,
            )
            if constraint is not None:
                raise WorkoutEnvironmentIncompatibleError(constraint)
            snapshots = session.scalars(
                select(WorkoutSessionExercise)
                .where(WorkoutSessionExercise.session_id == workout.id)
                .order_by(WorkoutSessionExercise.exercise_order)
            ).all()
            strict = (
                workout.plan_source == "user_defined"
                and workout.adaptation_mode == "strict"
            )
            replacements: list[tuple[WorkoutSessionExercise, Exercise]] = []
            used_ids: set[int] = set()
            for snapshot in snapshots:
                replacement = _environment_candidate(
                    session,
                    snapshot.planned_exercise_id,
                    training_environment=training_environment,
                    experience_level=profile.experience_level,
                    used_exercise_ids=used_ids,
                    strict=strict,
                )
                replacements.append((snapshot, replacement))
                used_ids.add(replacement.id)

            for snapshot, replacement in replacements:
                planned = replacement.id == snapshot.planned_exercise_id
                snapshot.selected_exercise_id = replacement.id
                snapshot.selected_exercise_name = (
                    snapshot.planned_exercise_name if planned else replacement.name
                )
                snapshot.selected_primary_muscle_group = (
                    snapshot.planned_primary_muscle_group
                    if planned else replacement.primary_muscle_group
                )
                snapshot.selected_hint = (
                    snapshot.planned_hint if planned else replacement.hint
                )
                snapshot.selected_target_sets = snapshot.planned_target_sets
                snapshot.selected_target_reps_min = snapshot.planned_target_reps_min
                snapshot.selected_target_reps_max = snapshot.planned_target_reps_max
                snapshot.selected_rest_seconds = snapshot.planned_rest_seconds
                snapshot.selected_progression_strategy = (
                    _environment_progression_strategy(
                        snapshot.planned_progression_strategy,
                        replacement,
                    )
                )
                snapshot.selected_format_reps = snapshot.planned_format_reps
                snapshot.selected_station_order = snapshot.planned_station_order
            workout.effective_training_environment = training_environment
            workout.updated_at = utc_now()
            session.flush()
            return _load_workout_view(session, workout)


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
    *,
    training_environment: str | None = None,
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
                profile = session.get(FitnessProfile, user_id)
                if (
                    profile is None
                    or profile.experience_level not in EXPERIENCE_LEVELS
                    or profile.training_environment not in TRAINING_ENVIRONMENTS
                ):
                    raise WorkoutEnvironmentIncompatibleError(
                        "A complete training profile is required."
                    )
                effective_environment = (
                    profile.training_environment
                    if training_environment is None
                    else training_environment
                )
                if effective_environment not in TRAINING_ENVIRONMENTS:
                    raise WorkoutEnvironmentIncompatibleError(
                        "Unsupported training environment."
                    )
                constraint = strength_profile_constraint_message(
                    goal=profile.goal,
                    experience_level=profile.experience_level,
                    training_environment=effective_environment,
                )
                if constraint is not None:
                    raise WorkoutEnvironmentIncompatibleError(constraint)

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
                    effective_environment,
                    profile.experience_level,
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


def get_workout_exercise_technique(
    user_id: int,
    session_exercise_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> ExerciseTechnique:
    """Load catalog-owned technique after checking snapshot ownership."""
    with session_factory() as session:
        snapshot = session.get(WorkoutSessionExercise, session_exercise_id)
        if snapshot is None:
            raise WorkoutNotFoundError("Workout exercise does not exist.")
        _require_owned_workout(session, user_id, snapshot.session_id)
        if snapshot.selected_exercise_id is None:
            raise WorkoutExecutionError("Exercise has no controlled catalog source.")
        code = session.scalar(
            select(Exercise.code).where(Exercise.id == snapshot.selected_exercise_id)
        )
        definition = exercise_definition_by_code(code or "")
        if definition is None:
            raise WorkoutExecutionError("Exercise technique is unavailable.")
        return definition.technique


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


def get_workout_history_page(
    user_id: int,
    *,
    offset: int = 0,
    page_size: int = 5,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutHistoryPage:
    """Load one completed-workout page without materializing all history."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise WorkoutExecutionError("History offset must be a non-negative integer.")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size < 1
        or page_size > 50
    ):
        raise WorkoutExecutionError("History page size is outside the supported range.")

    with session_factory() as session:
        rows = session.scalars(
            select(WorkoutSession)
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == "completed",
            )
            .order_by(WorkoutSession.finished_at.desc(), WorkoutSession.id.desc())
            .offset(offset)
            .limit(page_size + 1)
        ).all()
        has_older = len(rows) > page_size
        page_rows = rows[:page_size]
        return WorkoutHistoryPage(
            workouts=tuple(_load_workout_view(session, workout) for workout in page_rows),
            offset=offset,
            page_size=page_size,
            has_newer=offset > 0,
            has_older=has_older,
        )


def get_completed_workout_detail(
    user_id: int,
    workout_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutSessionView:
    """Return one owned completed snapshot, never a current-plan projection."""
    with session_factory() as session:
        workout = _require_owned_workout(session, user_id, workout_id)
        if workout.status != "completed":
            raise WorkoutStateError("Workout session is not completed.")
        return _load_workout_view(session, workout)
