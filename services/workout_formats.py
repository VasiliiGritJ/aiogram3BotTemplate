"""Durable execution and read-only progression for non-standard workout blocks."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    WorkoutFormatIntervalResult,
    WorkoutSession,
    WorkoutSessionBlock,
    WorkoutSessionExercise,
    dbSession,
)
from services.access import as_utc_naive, utc_now
from services.workout_execution import (
    WorkoutNotFoundError,
    WorkoutOwnershipError,
    WorkoutStateError,
)


class WorkoutFormat(StrEnum):
    STANDARD_SETS = "standard_sets"
    AMRAP = "amrap"
    EMOM = "emom"
    FOR_TIME = "for_time"
    CIRCUIT_ROUNDS = "circuit_rounds"


class FormatProgressionStrategy(StrEnum):
    AMRAP_SCORE = "amrap_score"
    EMOM_COMPLETION = "emom_completion"
    FOR_TIME = "for_time"
    CIRCUIT_ROUNDS = "circuit_rounds"


class WorkoutFormatError(RuntimeError):
    pass


@dataclass(frozen=True)
class FormatExercise:
    exercise_id: int
    station_order: int
    name: str
    reps: int


@dataclass(frozen=True)
class FormatBlockState:
    block_id: int
    session_id: int
    workout_format: WorkoutFormat
    title: str
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
    current_minute: int | None
    current_station_order: int | None
    exercises: tuple[FormatExercise, ...]


@dataclass(frozen=True)
class FormatMutationResult:
    state: FormatBlockState
    created: bool


@dataclass(frozen=True)
class FormatProgression:
    strategy: FormatProgressionStrategy
    previous_score: int | None
    previous_elapsed_seconds: int | None
    message_code: str


def _event_time(now: datetime | None) -> datetime:
    return as_utc_naive(now) if now is not None else utc_now()


def _owned_block(session: Session, user_id: int, block_id: int) -> tuple[WorkoutSession, WorkoutSessionBlock]:
    block = session.get(WorkoutSessionBlock, block_id)
    if block is None:
        raise WorkoutNotFoundError("Workout block does not exist.")
    workout = session.get(WorkoutSession, block.session_id)
    if workout is None:
        raise WorkoutNotFoundError("Workout session does not exist.")
    if workout.user_id != user_id:
        raise WorkoutOwnershipError("Workout block belongs to another user.")
    if block.workout_format == WorkoutFormat.STANDARD_SETS:
        raise WorkoutFormatError("Standard sets use the guided set service.")
    return workout, block


def _state(session: Session, block: WorkoutSessionBlock, now: datetime) -> FormatBlockState:
    rows = session.scalars(
        select(WorkoutSessionExercise)
        .where(WorkoutSessionExercise.session_block_id == block.id)
        .order_by(
            WorkoutSessionExercise.selected_station_order,
            WorkoutSessionExercise.exercise_order,
        )
    ).all()
    exercises = tuple(
        FormatExercise(
            exercise_id=row.id,
            station_order=row.selected_station_order or index,
            name=row.selected_exercise_name,
            reps=row.selected_format_reps or row.selected_target_reps_min,
        )
        for index, row in enumerate(rows, start=1)
    )
    current_minute = None
    current_station = None
    if (
        block.workout_format == WorkoutFormat.EMOM
        and block.started_at is not None
        and block.finished_at is None
        and exercises
    ):
        max_minutes = max(1, (block.duration_seconds or 60) // 60)
        current_minute = min(
            max_minutes,
            max(1, int((now - block.started_at).total_seconds() // 60) + 1),
        )
        current_station = exercises[(current_minute - 1) % len(exercises)].station_order
    return FormatBlockState(
        block_id=block.id,
        session_id=block.session_id,
        workout_format=WorkoutFormat(block.workout_format),
        title=block.title,
        duration_seconds=block.duration_seconds,
        target_rounds=block.target_rounds,
        started_at=block.started_at,
        finished_at=block.finished_at,
        completed_rounds=block.completed_rounds,
        partial_station_order=block.partial_station_order,
        partial_reps=block.partial_reps,
        completed_minutes=block.completed_minutes,
        missed_minutes=block.missed_minutes,
        elapsed_seconds=block.elapsed_seconds,
        final_score=block.final_score,
        current_minute=current_minute,
        current_station_order=current_station,
        exercises=exercises,
    )


def get_format_state(
    user_id: int,
    block_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatBlockState:
    event_time = _event_time(now)
    with session_factory() as session:
        _, block = _owned_block(session, user_id, block_id)
        return _state(session, block, event_time)


def start_format_block(
    user_id: int,
    block_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatMutationResult:
    event_time = _event_time(now)
    with session_factory() as session:
        with session.begin():
            workout, block = _owned_block(session, user_id, block_id)
            if workout.status != "in_progress" or block.finished_at is not None:
                raise WorkoutStateError("Workout block cannot be started.")
            created = block.started_at is None
            if created:
                block.started_at = event_time
                block.updated_at = event_time
                session.flush()
            return FormatMutationResult(_state(session, block, event_time), created)


def record_completed_round(
    user_id: int,
    block_id: int,
    expected_completed_rounds: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatMutationResult:
    event_time = _event_time(now)
    with session_factory() as session:
        with session.begin():
            workout, block = _owned_block(session, user_id, block_id)
            if workout.status != "in_progress" or block.started_at is None or block.finished_at is not None:
                raise WorkoutStateError("Workout block is not active.")
            if block.completed_rounds != expected_completed_rounds:
                return FormatMutationResult(_state(session, block, event_time), False)
            block.completed_rounds += 1
            block.updated_at = event_time
            session.flush()
            return FormatMutationResult(_state(session, block, event_time), True)


def record_emom_minute(
    user_id: int,
    block_id: int,
    minute_number: int,
    completed: bool,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatMutationResult:
    event_time = _event_time(now)
    if isinstance(minute_number, bool) or minute_number < 1:
        raise WorkoutFormatError("Minute number must be positive.")
    with session_factory() as session:
        with session.begin():
            workout, block = _owned_block(session, user_id, block_id)
            state = _state(session, block, event_time)
            if workout.status != "in_progress" or block.workout_format != WorkoutFormat.EMOM:
                raise WorkoutStateError("This block is not an active EMOM.")
            if block.started_at is None or block.finished_at is not None:
                raise WorkoutStateError("EMOM block is not active.")
            max_minutes = max(1, (block.duration_seconds or 60) // 60)
            if minute_number > max_minutes:
                raise WorkoutFormatError("Minute is outside the EMOM prescription.")
            existing = session.scalar(select(WorkoutFormatIntervalResult).where(
                WorkoutFormatIntervalResult.session_block_id == block.id,
                WorkoutFormatIntervalResult.minute_number == minute_number,
            ))
            if existing is not None:
                return FormatMutationResult(state, False)
            station = state.exercises[(minute_number - 1) % len(state.exercises)].station_order
            session.add(WorkoutFormatIntervalResult(
                session_block_id=block.id, minute_number=minute_number,
                station_order=station, completed=1 if completed else 0,
                recorded_at=event_time,
            ))
            if completed:
                block.completed_minutes += 1
            else:
                block.missed_minutes += 1
            block.updated_at = event_time
            session.flush()
            return FormatMutationResult(_state(session, block, event_time), True)


def finish_format_block(
    user_id: int,
    block_id: int,
    *,
    partial_station_order: int | None = None,
    partial_reps: int = 0,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatMutationResult:
    event_time = _event_time(now)
    if partial_reps < 0 or (partial_station_order is not None and partial_station_order < 1):
        raise WorkoutFormatError("Partial result is invalid.")
    with session_factory() as session:
        with session.begin():
            workout, block = _owned_block(session, user_id, block_id)
            if block.finished_at is not None:
                return FormatMutationResult(_state(session, block, event_time), False)
            if workout.status != "in_progress" or block.started_at is None:
                raise WorkoutStateError("Workout block is not active.")
            block.finished_at = event_time
            block.elapsed_seconds = max(0, int((event_time - block.started_at).total_seconds()))
            block.partial_station_order = partial_station_order
            block.partial_reps = partial_reps
            exercises = _state(session, block, event_time).exercises
            total_round_reps = sum(item.reps for item in exercises)
            if block.workout_format == WorkoutFormat.AMRAP:
                completed_partial_stations = sum(
                    item.reps for item in exercises
                    if partial_station_order is not None
                    and item.station_order < partial_station_order
                )
                current_station = next(
                    (
                        item for item in exercises
                        if item.station_order == partial_station_order
                    ),
                    None,
                )
                if partial_station_order is not None and current_station is None:
                    raise WorkoutFormatError("Partial station is outside the prescription.")
                if current_station is not None and partial_reps > current_station.reps:
                    raise WorkoutFormatError("Partial repetitions exceed the prescription.")
                block.final_score = (
                    block.completed_rounds * total_round_reps
                    + completed_partial_stations
                    + partial_reps
                )
            elif block.workout_format == WorkoutFormat.EMOM:
                block.final_score = block.completed_minutes
            else:
                block.final_score = block.completed_rounds
            block.updated_at = event_time
            session.flush()
            return FormatMutationResult(_state(session, block, event_time), True)


def get_format_progression(
    user_id: int,
    block_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> FormatProgression:
    with session_factory() as session:
        workout, block = _owned_block(session, user_id, block_id)
        strategy = {
            WorkoutFormat.AMRAP: FormatProgressionStrategy.AMRAP_SCORE,
            WorkoutFormat.EMOM: FormatProgressionStrategy.EMOM_COMPLETION,
            WorkoutFormat.FOR_TIME: FormatProgressionStrategy.FOR_TIME,
            WorkoutFormat.CIRCUIT_ROUNDS: FormatProgressionStrategy.CIRCUIT_ROUNDS,
        }[WorkoutFormat(block.workout_format)]
        structure = tuple(
            (row.selected_exercise_id, row.selected_format_reps, row.selected_station_order)
            for row in session.scalars(select(WorkoutSessionExercise).where(
                WorkoutSessionExercise.session_block_id == block.id
            ).order_by(WorkoutSessionExercise.selected_station_order)).all()
        )
        candidates = session.scalars(
            select(WorkoutSessionBlock)
            .join(WorkoutSession, WorkoutSession.id == WorkoutSessionBlock.session_id)
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == "completed",
                WorkoutSession.finished_at < workout.started_at,
                WorkoutSessionBlock.workout_format == block.workout_format,
                WorkoutSessionBlock.duration_seconds.is_(block.duration_seconds),
                WorkoutSessionBlock.target_rounds.is_(block.target_rounds),
            )
            .order_by(WorkoutSession.finished_at.desc(), WorkoutSession.id.desc())
            .limit(10)
        ).all()
        previous = None
        for candidate in candidates:
            candidate_structure = tuple(
                (row.selected_exercise_id, row.selected_format_reps, row.selected_station_order)
                for row in session.scalars(select(WorkoutSessionExercise).where(
                    WorkoutSessionExercise.session_block_id == candidate.id
                ).order_by(WorkoutSessionExercise.selected_station_order)).all()
            )
            if candidate_structure == structure:
                previous = candidate
                break
        if previous is None:
            return FormatProgression(strategy, None, None, "no_history")
        code = "beat_score" if strategy != FormatProgressionStrategy.FOR_TIME else "beat_time"
        return FormatProgression(strategy, previous.final_score, previous.elapsed_seconds, code)
