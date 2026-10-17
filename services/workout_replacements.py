"""Controlled, deterministic runtime replacement for workout snapshots."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Exercise,
    FitnessProfile,
    WorkoutSession,
    WorkoutSessionBlock,
    WorkoutSessionExercise,
    WorkoutSetResult,
    dbSession,
)
from services.exercise_catalog import (
    EXERCISE_DEFINITIONS,
    EXPERIENCE_LEVELS,
    TRAINING_ENVIRONMENTS,
    ExerciseDefinition,
)
from services.workout_execution import (
    WorkoutExecutionError,
    WorkoutNotFoundError,
    WorkoutOwnershipError,
    WorkoutStateError,
)


MAX_REPLACEMENT_CANDIDATES = 3


class ReplacementReason(StrEnum):
    STRICT_MODE = "strict_mode"
    UNAVAILABLE = "replacement_unavailable"
    EXERCISE_STARTED = "exercise_started"
    STALE_STEP = "stale_step"
    TIMED_BLOCK_STARTED = "timed_block_started"
    INVALID_CANDIDATE = "invalid_candidate"
    ALREADY_REPLACED = "already_replaced"


class WorkoutReplacementError(WorkoutExecutionError):
    """Base exception for a controlled replacement action."""


class ReplacementNotAllowedError(WorkoutReplacementError):
    def __init__(self, reason: ReplacementReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ReplacementCandidate:
    exercise_id: int
    exercise_code: str
    name: str
    primary_muscle_group: str
    equipment: str


@dataclass(frozen=True)
class ReplacementOptions:
    session_exercise_id: int
    current_exercise_name: str
    candidates: tuple[ReplacementCandidate, ...]


@dataclass(frozen=True)
class ReplacementResult:
    session_exercise_id: int
    planned_exercise_id: int | None
    selected_exercise_id: int | None
    selected_exercise_name: str
    created: bool


_DEFINITIONS_BY_CODE = {item.code: item for item in EXERCISE_DEFINITIONS}


def rank_replacement_definitions(
    current: ExerciseDefinition,
    *,
    training_environment: str,
    experience_level: str,
    definitions: Iterable[ExerciseDefinition] = EXERCISE_DEFINITIONS,
    limit: int = MAX_REPLACEMENT_CANDIDATES,
) -> tuple[ExerciseDefinition, ...]:
    """Return a small, deterministic and deliberately strict candidate list.

    An equivalence group is the controlled taxonomy's explicit statement that
    two movements may substitute one another.  It is a hard safety boundary,
    not merely a ranking preference.
    """
    if training_environment not in TRAINING_ENVIRONMENTS:
        return ()
    if experience_level not in EXPERIENCE_LEVELS:
        return ()
    if limit < 1:
        return ()

    candidates = [
        item
        for item in definitions
        if item.code != current.code
        and item.equivalence_group == current.equivalence_group
        and item.primary_muscle_group == current.primary_muscle_group
        and item.movement_pattern == current.movement_pattern
        and item.progression_type == current.progression_type
        and training_environment in item.environments
        and experience_level in item.experience_levels
    ]
    candidates.sort(
        key=lambda item: (
            0 if item.equipment == current.equipment else 1,
            item.code,
        )
    )
    return tuple(candidates[:limit])


def _require_owned_workout(
    session: Session,
    user_id: int,
    session_exercise_id: int,
) -> tuple[WorkoutSession, WorkoutSessionExercise]:
    snapshot = session.get(WorkoutSessionExercise, session_exercise_id)
    if snapshot is None:
        raise WorkoutNotFoundError("Workout exercise does not exist.")
    workout = session.get(WorkoutSession, snapshot.session_id)
    if workout is None:
        raise WorkoutNotFoundError("Workout session does not exist.")
    if workout.user_id != user_id:
        raise WorkoutOwnershipError("Workout exercise belongs to another user.")
    if workout.status != "in_progress":
        raise WorkoutStateError("Workout session is not in progress.")
    return workout, snapshot


def _assert_mode_allows_replacement(workout: WorkoutSession) -> None:
    if workout.plan_source == "user_defined" and workout.adaptation_mode == "strict":
        raise ReplacementNotAllowedError(
            ReplacementReason.STRICT_MODE,
            "This user program requires strict execution.",
        )


def _has_saved_set(session: Session, snapshot: WorkoutSessionExercise) -> bool:
    return session.scalar(
        select(WorkoutSetResult.id)
        .where(WorkoutSetResult.session_exercise_id == snapshot.id)
        .limit(1)
    ) is not None


def _current_standard_snapshot(
    session: Session,
    workout: WorkoutSession,
) -> WorkoutSessionExercise | None:
    snapshots = session.scalars(
        select(WorkoutSessionExercise)
        .where(WorkoutSessionExercise.session_id == workout.id)
        .order_by(WorkoutSessionExercise.exercise_order)
    ).all()
    for snapshot in snapshots:
        if snapshot.session_block_id is not None:
            block = session.get(WorkoutSessionBlock, snapshot.session_block_id)
            if block is not None and block.workout_format != "standard_sets":
                continue
        completed = set(session.scalars(
            select(WorkoutSetResult.set_number).where(
                WorkoutSetResult.session_exercise_id == snapshot.id
            )
        ).all())
        if any(number not in completed for number in range(1, snapshot.selected_target_sets + 1)):
            return snapshot
    return None


def _assert_current_and_unstarted(
    session: Session,
    workout: WorkoutSession,
    snapshot: WorkoutSessionExercise,
) -> None:
    """Ensure a callback targets the durable current cursor before mutation."""
    if snapshot.session_block_id is None:
        if _has_saved_set(session, snapshot):
            raise ReplacementNotAllowedError(
                ReplacementReason.EXERCISE_STARTED,
                "Exercise already has saved sets.",
            )
        if _current_standard_snapshot(session, workout) is not snapshot:
            raise ReplacementNotAllowedError(
                ReplacementReason.STALE_STEP,
                "Exercise is no longer the current step.",
            )
        return

    block = session.get(WorkoutSessionBlock, snapshot.session_block_id)
    if block is None:
        raise ReplacementNotAllowedError(
            ReplacementReason.STALE_STEP,
            "Workout block is unavailable.",
        )
    if block.workout_format == "standard_sets":
        if _has_saved_set(session, snapshot):
            raise ReplacementNotAllowedError(
                ReplacementReason.EXERCISE_STARTED,
                "Exercise already has saved sets.",
            )
        if _current_standard_snapshot(session, workout) is not snapshot:
            raise ReplacementNotAllowedError(
                ReplacementReason.STALE_STEP,
                "Exercise is no longer the current step.",
            )
        return
    if block.started_at is not None or block.finished_at is not None:
        raise ReplacementNotAllowedError(
            ReplacementReason.TIMED_BLOCK_STARTED,
            "Timed workout block has already started.",
        )
    if _current_standard_snapshot(session, workout) is not None:
        raise ReplacementNotAllowedError(
            ReplacementReason.STALE_STEP,
            "A standard exercise must be completed first.",
        )
    current_block = session.scalar(
        select(WorkoutSessionBlock)
        .where(
            WorkoutSessionBlock.session_id == workout.id,
            WorkoutSessionBlock.workout_format != "standard_sets",
            WorkoutSessionBlock.finished_at.is_(None),
        )
        .order_by(WorkoutSessionBlock.block_order)
    )
    if current_block is None or current_block.id != block.id:
        raise ReplacementNotAllowedError(
            ReplacementReason.STALE_STEP,
            "Workout block is no longer current.",
        )


def _source_definition(session: Session, snapshot: WorkoutSessionExercise) -> ExerciseDefinition:
    if snapshot.selected_exercise_id is None:
        raise ReplacementNotAllowedError(
            ReplacementReason.UNAVAILABLE,
            "Current exercise has no controlled taxonomy identity.",
        )
    exercise = session.get(Exercise, snapshot.selected_exercise_id)
    definition = None if exercise is None else _DEFINITIONS_BY_CODE.get(exercise.code)
    if definition is None:
        raise ReplacementNotAllowedError(
            ReplacementReason.UNAVAILABLE,
            "Current exercise is not available for controlled replacement.",
        )
    return definition


def _profile_context(
    session: Session,
    user_id: int,
    workout: WorkoutSession,
) -> tuple[str, str]:
    profile = session.get(FitnessProfile, user_id)
    environment = workout.effective_training_environment or (
        None if profile is None else profile.training_environment
    )
    if (
        profile is None
        or environment not in TRAINING_ENVIRONMENTS
        or profile.experience_level not in EXPERIENCE_LEVELS
    ):
        raise ReplacementNotAllowedError(
            ReplacementReason.UNAVAILABLE,
            "Training profile is insufficient for a safe replacement.",
        )
    return environment, profile.experience_level


def _candidate_models(
    session: Session,
    source: ExerciseDefinition,
    *,
    training_environment: str,
    experience_level: str,
) -> tuple[tuple[ExerciseDefinition, Exercise], ...]:
    definitions = rank_replacement_definitions(
        source,
        training_environment=training_environment,
        experience_level=experience_level,
        limit=len(EXERCISE_DEFINITIONS),
    )
    if not definitions:
        return ()
    rows = session.scalars(
        select(Exercise).where(Exercise.code.in_([item.code for item in definitions]))
    ).all()
    by_code = {item.code: item for item in rows}
    candidates = [
        (definition, by_code[definition.code])
        for definition in definitions
        if definition.code in by_code
    ]
    candidates.sort(
        key=lambda item: (
            0 if item[0].equipment == source.equipment else 1,
            item[1].id,
        )
    )
    return tuple(candidates[:MAX_REPLACEMENT_CANDIDATES])


def get_replacement_options(
    user_id: int,
    session_exercise_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> ReplacementOptions:
    """Read current candidates from durable state; never mutate the workout."""
    with session_factory() as session:
        workout, snapshot = _require_owned_workout(session, user_id, session_exercise_id)
        _assert_mode_allows_replacement(workout)
        if (
            snapshot.planned_exercise_id is not None
            and snapshot.selected_exercise_id != snapshot.planned_exercise_id
        ):
            raise ReplacementNotAllowedError(
                ReplacementReason.ALREADY_REPLACED,
                "This session exercise has already been replaced.",
            )
        _assert_current_and_unstarted(session, workout, snapshot)
        source = _source_definition(session, snapshot)
        environment, experience = _profile_context(session, user_id, workout)
        candidates = _candidate_models(
            session,
            source,
            training_environment=environment,
            experience_level=experience,
        )
        if not candidates:
            raise ReplacementNotAllowedError(
                ReplacementReason.UNAVAILABLE,
                "No safe controlled replacement is available.",
            )
        return ReplacementOptions(
            session_exercise_id=snapshot.id,
            current_exercise_name=snapshot.selected_exercise_name,
            candidates=tuple(
                ReplacementCandidate(
                    exercise_id=row.id,
                    exercise_code=definition.code,
                    name=row.name,
                    primary_muscle_group=row.primary_muscle_group,
                    equipment=row.equipment,
                )
                for definition, row in candidates
            ),
        )


def apply_replacement(
    user_id: int,
    session_exercise_id: int,
    replacement_exercise_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> ReplacementResult:
    """Change only selected snapshot fields, exactly once and before work starts."""
    with session_factory() as session:
        with session.begin():
            workout, snapshot = _require_owned_workout(session, user_id, session_exercise_id)
            _assert_mode_allows_replacement(workout)
            if snapshot.selected_exercise_id == replacement_exercise_id:
                return ReplacementResult(
                    snapshot.id,
                    snapshot.planned_exercise_id,
                    snapshot.selected_exercise_id,
                    snapshot.selected_exercise_name,
                    False,
                )
            if (
                snapshot.planned_exercise_id is not None
                and snapshot.selected_exercise_id != snapshot.planned_exercise_id
            ):
                raise ReplacementNotAllowedError(
                    ReplacementReason.ALREADY_REPLACED,
                    "This session exercise has already been replaced.",
                )
            _assert_current_and_unstarted(session, workout, snapshot)
            source = _source_definition(session, snapshot)
            environment, experience = _profile_context(session, user_id, workout)
            candidates = _candidate_models(
                session,
                source,
                training_environment=environment,
                experience_level=experience,
            )
            selected = next(
                (
                    (definition, row)
                    for definition, row in candidates
                    if row.id == replacement_exercise_id
                ),
                None,
            )
            if selected is None:
                raise ReplacementNotAllowedError(
                    ReplacementReason.INVALID_CANDIDATE,
                    "Replacement is not a current safe candidate.",
                )
            _, replacement = selected
            snapshot.selected_exercise_id = replacement.id
            snapshot.selected_exercise_name = replacement.name
            snapshot.selected_primary_muscle_group = replacement.primary_muscle_group
            snapshot.selected_hint = replacement.hint
            # Prescription/order remain the current session's durable intent.
            # Candidate capability is strict-equal, so preserving the strategy
            # keeps strict/replacements/adaptive semantics intact.
            session.flush()
            return ReplacementResult(
                snapshot.id,
                snapshot.planned_exercise_id,
                snapshot.selected_exercise_id,
                snapshot.selected_exercise_name,
                True,
            )
