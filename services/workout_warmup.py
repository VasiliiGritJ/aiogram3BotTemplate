"""Deterministic, snapshot-derived preparation for a workout.

Warm-up instructions are deliberately derived from the persisted selected
exercise snapshot.  They are UI/planning guidance only: no warm-up action is
stored as a ``WorkoutSetResult`` or passed to progression/history services.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable

from services.exercise_catalog import exercise_definition_by_code


@dataclass(frozen=True)
class WarmupExercise:
    """The small, immutable exercise projection needed by the warm-up rules."""

    identity: int | str
    name: str
    code: str | None
    primary_muscle_group: str
    target_reps_min: int
    rest_seconds: int
    progression_strategy: str | None
    is_timed: bool = False


@dataclass(frozen=True)
class WarmupAction:
    title: str
    instruction: str
    estimated_minutes: int


@dataclass(frozen=True)
class RampUpPlan:
    """Preparation for one upcoming exercise, separate from working sets."""

    exercise_identity: int | str
    exercise_name: str
    set_count: int
    instructions: tuple[str, ...]
    kind: str


@dataclass(frozen=True)
class WorkoutWarmupPlan:
    general_preparation: tuple[WarmupAction, ...]
    movement_preparation: tuple[WarmupAction, ...]
    ramp_up_sets: tuple[RampUpPlan, ...]
    estimated_minutes: int

    def ramp_for(self, exercise_identity: int | str) -> RampUpPlan | None:
        return next(
            (
                item
                for item in self.ramp_up_sets
                if item.exercise_identity == exercise_identity
            ),
            None,
        )


_MOVEMENT_FAMILIES = {
    "squat": "lower_knee",
    "lunge": "lower_knee",
    "hinge": "hinge",
    "horizontal_push": "press",
    "vertical_push": "press",
    "horizontal_pull": "pull",
    "vertical_pull": "pull",
    "scapular_rear_delt": "pull",
    "locomotion_conditioning": "conditioning",
}

_MOVEMENT_ACTIONS = {
    "lower_knee": WarmupAction(
        "Подготовка ног",
        "8–10 лёгких приседаний и несколько контролируемых движений голеностопа.",
        1,
    ),
    "hinge": WarmupAction(
        "Подготовка тазобедренного движения",
        "8–10 спокойных наклонов: таз назад, спина нейтральна, амплитуда комфортная.",
        1,
    ),
    "press": WarmupAction(
        "Подготовка жима",
        "8–10 контролируемых движений плечами и лёгкая репетиция траектории жима.",
        1,
    ),
    "pull": WarmupAction(
        "Подготовка тяги",
        "8–10 мягких сведений и опусканий лопаток, затем лёгкая репетиция тяги.",
        1,
    ),
    "conditioning": WarmupAction(
        "Репетиция формата",
        "Сделайте короткую спокойную репетицию движений предстоящего блока без спешки.",
        1,
    ),
}


def _fallback_movement(primary_muscle_group: str) -> str:
    label = primary_muscle_group.casefold()
    if label in {"грудь", "chest", "плечи", "shoulders", "трицепс", "triceps"}:
        return "horizontal_push"
    if label in {"спина", "back", "бицепс", "biceps"}:
        return "horizontal_pull"
    if label in {"квадрицепс", "quads", "икры", "calves"}:
        return "squat"
    if label in {"ягодицы", "glutes", "задняя поверхность бедра", "hamstrings"}:
        return "hinge"
    return "core"


def _exercise_traits(exercise: WarmupExercise) -> tuple[str, str, str]:
    definition = exercise_definition_by_code(exercise.code or "")
    if definition is not None:
        return (
            definition.movement_pattern,
            definition.equipment,
            definition.progression_type,
        )
    progression = (
        "bodyweight_reps"
        if exercise.progression_strategy == "bodyweight_reps"
        else "external_load_reps"
    )
    return _fallback_movement(exercise.primary_muscle_group), "unknown", progression


def _general_preparation(environment: str, duration_minutes: int) -> WarmupAction:
    minutes = 2 if duration_minutes <= 30 else (4 if duration_minutes >= 90 else 3)
    instructions = {
        "gym": "Лёгкая дорожка, велосипед или гребля в разговорном темпе.",
        "functional_gym": "Лёгкое циклическое движение в разговорном темпе: bike, rower или аналог.",
        "street": "Лёгкая ходьба на месте и динамическая активация без оборудования.",
        "home": "Лёгкая ходьба на месте и динамическая активация без оборудования.",
    }
    return WarmupAction(
        "Общая активация",
        f"{minutes}–{minutes + 1} мин. {instructions.get(environment, instructions['home'])}",
        minutes,
    )


def _ramp_instructions(
    *,
    count: int,
    progression_type: str,
    target_reps_min: int,
) -> tuple[str, ...]:
    if progression_type == "bodyweight_reps":
        repetitions = max(3, min(8, target_reps_min // 2))
        return (
            f"{repetitions} лёгких повторений или упрощённый вариант движения.",
        )
    if count >= 3:
        return (
            "8 повторений с очень лёгким весом.",
            "5 повторений с умеренным весом.",
            "2–3 повторения почти с рабочим весом, без утомления.",
        )
    if count == 2:
        return (
            "8 лёгких повторений.",
            "4–5 повторений с умеренным весом.",
        )
    return ("8–10 лёгких повторений: постепенно подберите рабочий вес.",)


def _ramp_plan(
    exercise: WarmupExercise,
    *,
    seen_families: set[str],
) -> RampUpPlan | None:
    movement, equipment, progression_type = _exercise_traits(exercise)
    family = _MOVEMENT_FAMILIES.get(movement)
    if exercise.is_timed or movement in {"core", "carry", "locomotion_conditioning"}:
        return None
    if progression_type == "bodyweight_reps":
        if movement in {"isolation", "scapular_rear_delt"}:
            return None
        return RampUpPlan(
            exercise.identity,
            exercise.name,
            1,
            _ramp_instructions(
                count=1,
                progression_type=progression_type,
                target_reps_min=exercise.target_reps_min,
            ),
            "bodyweight_rehearsal",
        )
    if movement in {"isolation", "scapular_rear_delt"}:
        return None
    first_for_family = family is not None and family not in seen_families
    heavy_strength = (
        exercise.progression_strategy == "strength_load_reps"
        and equipment in {"barbell", "dumbbell", "smith"}
    )
    if heavy_strength:
        count = 3
    elif equipment in {"machine", "cable"}:
        count = 1 if first_for_family else 0
    elif first_for_family:
        count = 2
    else:
        count = 1 if exercise.rest_seconds >= 120 else 0
    if count == 0:
        return None
    return RampUpPlan(
        exercise.identity,
        exercise.name,
        count,
        _ramp_instructions(
            count=count,
            progression_type=progression_type,
            target_reps_min=exercise.target_reps_min,
        ),
        "external_load",
    )


def build_warmup_plan(
    *,
    environment: str,
    exercises: Iterable[WarmupExercise],
    duration_minutes: int = 60,
) -> WorkoutWarmupPlan:
    """Create deterministic preparation without treating it as working volume."""
    items = tuple(exercises)
    general = (_general_preparation(environment, duration_minutes),)
    movement_limit = 2 if duration_minutes <= 30 else 3
    movement_actions: list[WarmupAction] = []
    seen_families: set[str] = set()
    for item in items:
        movement, _, _ = _exercise_traits(item)
        family = _MOVEMENT_FAMILIES.get(movement)
        if family is None or family in seen_families:
            continue
        seen_families.add(family)
        action = _MOVEMENT_ACTIONS[family]
        if len(movement_actions) < movement_limit:
            movement_actions.append(action)

    seen_ramp_families: set[str] = set()
    ramps: list[RampUpPlan] = []
    for item in items:
        ramp = _ramp_plan(item, seen_families=seen_ramp_families)
        movement, _, _ = _exercise_traits(item)
        family = _MOVEMENT_FAMILIES.get(movement)
        if family is not None:
            seen_ramp_families.add(family)
        if ramp is not None:
            ramps.append(ramp)
    preparation_minutes = sum(item.estimated_minutes for item in general + tuple(movement_actions))
    ramp_minutes = ceil(sum(item.set_count for item in ramps) / 2) if ramps else 0
    return WorkoutWarmupPlan(
        general_preparation=general,
        movement_preparation=tuple(movement_actions),
        ramp_up_sets=tuple(ramps),
        estimated_minutes=preparation_minutes + ramp_minutes,
    )


def build_generated_day_warmup(profile, day) -> WorkoutWarmupPlan:
    """Adapt a generated day without importing the planning module (no cycle)."""
    exercises: list[WarmupExercise] = []
    for index, item in enumerate(day.exercises, start=1):
        exercises.append(WarmupExercise(
            identity=item.exercise_code,
            name=(exercise_definition_by_code(item.exercise_code).name),
            code=item.exercise_code,
            primary_muscle_group=(exercise_definition_by_code(item.exercise_code).primary_muscle_group),
            target_reps_min=item.reps_min,
            rest_seconds=item.rest_seconds,
            progression_strategy=str(getattr(item.progression_strategy, "value", item.progression_strategy)),
        ))
    for block in day.blocks:
        for item in block.exercises:
            definition = exercise_definition_by_code(item.exercise_code)
            exercises.append(WarmupExercise(
                identity=f"{block.title}:{item.exercise_code}",
                name=definition.name,
                code=item.exercise_code,
                primary_muscle_group=definition.primary_muscle_group,
                target_reps_min=item.reps,
                rest_seconds=0,
                progression_strategy="timed_conditioning",
                is_timed=True,
            ))
    return build_warmup_plan(
        environment=profile.training_environment,
        exercises=exercises,
        duration_minutes=profile.session_duration_minutes,
    )


def build_session_warmup(workout) -> WorkoutWarmupPlan:
    """Derive guidance from selected immutable session snapshots only."""
    exercises: list[WarmupExercise] = []
    for item in workout.exercises:
        if item.session_block_id is not None:
            continue
        exercises.append(WarmupExercise(
            identity=item.id,
            name=item.selected_exercise_name,
            code=getattr(item, "selected_exercise_code", None),
            primary_muscle_group=item.selected_primary_muscle_group,
            target_reps_min=item.selected_target_reps_min,
            rest_seconds=item.selected_rest_seconds,
            progression_strategy=str(getattr(item.selected_progression_strategy, "value", item.selected_progression_strategy)),
        ))
    for block in workout.blocks:
        for item in block.exercises:
            exercises.append(WarmupExercise(
                identity=item.id,
                name=item.selected_exercise_name,
                code=getattr(item, "selected_exercise_code", None),
                primary_muscle_group=item.selected_primary_muscle_group,
                target_reps_min=item.selected_format_reps or item.selected_target_reps_min,
                rest_seconds=0,
                progression_strategy="timed_conditioning",
                is_timed=True,
            ))
    return build_warmup_plan(
        environment=workout.effective_training_environment or "home",
        exercises=exercises,
    )
