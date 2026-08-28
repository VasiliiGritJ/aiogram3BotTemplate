"""Controlled exercise catalog and deterministic weekly workout plans."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import math
from functools import lru_cache
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import (
    Exercise,
    FitnessProfile,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanBlock,
    UserWorkoutPlanExercise,
    WorkoutTemplate,
    WorkoutTemplateDay,
    WorkoutTemplateExercise,
    dbSession,
)
from services.exercise_catalog import (
    EXERCISE_ALTERNATIVES,
    EXERCISE_DEFINITIONS,
    ExerciseDefinition,
    exercise_definition_by_code,
    validate_exercise_definition,
)
from services.workout_progression import ProgressionStrategy
from services.workout_warmup import build_generated_day_warmup


CATALOG_VERSION = 6
DEFAULT_GOAL = "muscle_gain"
DEFAULT_EXPERIENCE = "beginner"
DEFAULT_EQUIPMENT = "gym"
ADAPTIVE_TEMPLATE_CODE = "v4_adaptive_formats"
# Legacy controlled templates are retained for existing assigned-plan references.
# New Stage 7C assignments use the adaptive rule-based program below.
MAX_TEMPLATE_WORKOUTS_PER_WEEK = 4
SHORT_SESSION_MAX_MINUTES = 45
HOME_PULL_LIMITATION_NOTICE = (
    "Без турника или резинки возможности для полноценной тренировки "
    "тяговых мышц ограничены."
)
HOME_RELATIVE_STRENGTH_LIMITATION_NOTICE = (
    "Для силовой тренировки дома без оборудования на этом уровне "
    "недостаточно безопасных вариантов усложнения. Лучше выбрать улицу, "
    "функциональный или тренажёрный зал."
)

SUPPORTED_GOALS = {"muscle_gain", "strength", "fat_loss"}
SUPPORTED_EXPERIENCE = {"beginner", "intermediate", "advanced"}
LEGACY_TEMPLATE_GOALS = {"muscle_gain", "fat_loss"}
LEGACY_TEMPLATE_EXPERIENCE = {"beginner", "some_experience"}
SUPPORTED_ENVIRONMENTS = {"gym", "functional_gym", "street", "home"}
SUPPORTED_FREQUENCIES = {2, 3, 4, 5, 6}
SUPPORTED_DURATIONS = {30, 45, 60, 90}
# Thirty minutes retain three complementary priority movements, but their
# prescriptions are compact so required preparation is not squeezed out.
DURATION_EXERCISE_BUDGETS = {30: 3, 45: 4, 60: 5, 90: 6}
DURATION_FIT_MIN_RATIO = 0.80
DURATION_FIT_MAX_RATIO = 1.10

GOAL_NAMES = {
    "muscle_gain": "Набор мышечной массы",
    "strength": "Силовая программа",
    "fat_loss": "Снижение процента жира",
}
EXPERIENCE_NAMES = {
    "beginner": "новичок",
    "intermediate": "средний",
    "advanced": "продвинутый",
    "some_experience": "с опытом",
}

class WorkoutPlanError(RuntimeError):
    """Base error for deterministic workout planning."""


class FitnessProfileRequiredError(WorkoutPlanError):
    """Raised when a workout plan is requested before onboarding."""


class WorkoutPlanNotReadyError(WorkoutPlanError):
    """Raised when Stage 7 profile choices need the Stage 7C generator."""


class WorkoutDurationUnsupportedError(WorkoutPlanNotReadyError):
    """The selected duration cannot be filled honestly for this profile."""


class WorkoutStrengthProfileUnsupportedError(WorkoutPlanNotReadyError):
    """The controlled catalog cannot support this relative-strength profile."""


class WorkoutCatalogError(WorkoutPlanError):
    """Raised when the controlled catalog is incomplete or inconsistent."""


@dataclass(frozen=True)
class TemplateExerciseDefinition:
    exercise_code: str
    sets: int
    reps_min: int
    reps_max: int
    rest_seconds: int


@dataclass(frozen=True)
class TemplateDayDefinition:
    day_number: int
    title: str
    exercises: tuple[TemplateExerciseDefinition, ...]


@dataclass(frozen=True)
class TemplateDefinition:
    code: str
    name: str
    goal: str
    experience_level: str
    workouts_per_week: int
    duration_bucket: str
    equipment: str
    days: tuple[TemplateDayDefinition, ...]


@dataclass(frozen=True)
class NormalizedProfile:
    goal: str
    experience_level: str
    training_environment: str
    workouts_per_week: int
    session_duration_minutes: int
    equipment: str
    has_limitations: bool
    fallback_notes: tuple[str, ...]


@dataclass(frozen=True)
class PlanExerciseView:
    order: int
    name: str
    primary_muscle_group: str
    sets: int
    reps_min: int
    reps_max: int
    rest_seconds: int
    hint: str
    progression_strategy: str | None = None
    workout_format: str = "standard_sets"
    format_reps: int | None = None
    station_order: int | None = None


@dataclass(frozen=True)
class PlanBlockView:
    order: int
    title: str
    workout_format: str
    duration_seconds: int | None
    target_rounds: int | None
    exercises: tuple[PlanExerciseView, ...]


@dataclass(frozen=True)
class PlanDayView:
    day_number: int
    title: str
    exercises: tuple[PlanExerciseView, ...]
    blocks: tuple[PlanBlockView, ...] = ()


@dataclass(frozen=True)
class WorkoutPlanView:
    id: int
    template_name: str
    days: tuple[PlanDayView, ...]


@dataclass(frozen=True)
class PlanAssignmentResult:
    plan: WorkoutPlanView
    created: bool
    fallback_notes: tuple[str, ...]


@dataclass(frozen=True)
class CatalogStats:
    exercises: int
    templates: int
    template_days: int
    template_exercises: int


@dataclass(frozen=True)
class GeneratedExerciseDefinition:
    exercise_code: str
    sets: int
    reps_min: int
    reps_max: int
    rest_seconds: int
    progression_strategy: str


@dataclass(frozen=True)
class GeneratedDayDefinition:
    day_number: int
    title: str
    exercises: tuple[GeneratedExerciseDefinition, ...]
    blocks: tuple["GeneratedBlockDefinition", ...] = ()


@dataclass(frozen=True)
class GeneratedFormatExerciseDefinition:
    exercise_code: str
    reps: int


@dataclass(frozen=True)
class GeneratedBlockDefinition:
    title: str
    workout_format: str
    duration_seconds: int | None
    target_rounds: int | None
    exercises: tuple[GeneratedFormatExerciseDefinition, ...]


@dataclass(frozen=True)
class GeneratedProgramDefinition:
    name: str
    days: tuple[GeneratedDayDefinition, ...]


@dataclass(frozen=True)
class ProgramDayBlueprint:
    """A deterministic weekly training purpose before exercise selection."""

    title: str
    slots: tuple[str, ...]
    include_conditioning: bool = False


# These are intentionally catalog-code ladders rather than made-up exercise
# names.  A level only receives a bodyweight strength blueprint when the
# catalog contains a credible harder stage for its key movements.
RELATIVE_STRENGTH_LADDERS = {
    "home": {
        "beginner": (
            "bodyweight_squat",
            "push_up",
            "bodyweight_glute_bridge",
        ),
        # The no-equipment catalog has no controlled harder push successor
        # beyond a standard floor push-up.  Intermediate and advanced home
        # strength therefore fail closed instead of pretending that fewer reps
        # of the same movement are a strength progression.
        "intermediate": (),
        "advanced": (),
    },
    "street": {
        "beginner": (
            "bodyweight_squat",
            "incline_push_up",
            "inverted_row",
        ),
        "intermediate": (
            "reverse_lunge",
            "pull_up",
            "parallel_bar_dip",
            "single_leg_glute_bridge",
        ),
        "advanced": (
            "reverse_lunge",
            "chin_up",
            "parallel_bar_dip",
            "single_leg_glute_bridge",
        ),
    },
}


DAY_BLUEPRINTS = {
    "muscle_gain": (
        (
            "barbell_back_squat",
            "barbell_bench_press",
            "lat_pulldown",
            "seated_leg_curl",
            "cable_curl",
            "cable_crunch",
        ),
        (
            "hip_abduction",
            "seated_row",
            "shoulder_press",
            "calf_raise",
            "triceps_pushdown",
            "back_extension",
        ),
        (
            "barbell_back_squat",
            "barbell_bench_press",
            "seated_row",
            "hip_abduction",
            "cable_curl",
            "back_extension",
        ),
        (
            "seated_leg_curl",
            "lat_pulldown",
            "shoulder_press",
            "calf_raise",
            "triceps_pushdown",
            "cable_crunch",
        ),
    ),
    "fat_loss": (
        (
            "barbell_back_squat",
            "lat_pulldown",
            "chest_press",
            "hip_abduction",
            "cable_curl",
            "cable_crunch",
        ),
        (
            "seated_leg_curl",
            "seated_row",
            "shoulder_press",
            "calf_raise",
            "triceps_pushdown",
            "back_extension",
        ),
        (
            "barbell_back_squat",
            "barbell_bench_press",
            "seated_row",
            "seated_leg_curl",
            "triceps_pushdown",
            "cable_crunch",
        ),
        (
            "hip_abduction",
            "lat_pulldown",
            "shoulder_press",
            "calf_raise",
            "cable_curl",
            "back_extension",
        ),
    ),
}


NO_LIMITATIONS_VALUES = {
    "",
    "нет",
    "нету",
    "нет ограничений",
    "отсутствуют",
    "no",
    "none",
    "-",
}
LIMITATIONS_NOTICE = (
    "Указанные ограничения сохранены в профиле. Если нагрузка вызывает боль "
    "или у вас есть медицинские противопоказания, скорректируйте тренировку "
    "со специалистом."
)


def _template_code(
    goal: str,
    experience_level: str,
    workouts_per_week: int,
    duration_bucket: str,
) -> str:
    return (
        f"v{CATALOG_VERSION}_{goal}_{experience_level}_"
        f"{workouts_per_week}_{duration_bucket}_{DEFAULT_EQUIPMENT}"
    )


def _exercise_targets(
    goal: str,
    experience_level: str,
) -> tuple[int, int, int, int]:
    sets = 2 if experience_level == "beginner" else 3
    if goal == "fat_loss":
        return sets, 10, 15, 60
    return sets, 8, 12, 90


def build_template_definitions() -> tuple[TemplateDefinition, ...]:
    """Build a finite, versioned matrix from controlled day blueprints."""
    definitions: list[TemplateDefinition] = []
    for goal in sorted(LEGACY_TEMPLATE_GOALS):
        for experience_level in sorted(LEGACY_TEMPLATE_EXPERIENCE):
            sets, reps_min, reps_max, rest_seconds = _exercise_targets(
                goal,
                experience_level,
            )
            for workouts_per_week in range(1, MAX_TEMPLATE_WORKOUTS_PER_WEEK + 1):
                for duration_bucket in ("short", "standard"):
                    exercise_limit = 4 if duration_bucket == "short" else 6
                    days: list[TemplateDayDefinition] = []
                    for day_index, blueprint in enumerate(
                        DAY_BLUEPRINTS[goal][:workouts_per_week],
                        start=1,
                    ):
                        exercises = tuple(
                            TemplateExerciseDefinition(
                                exercise_code=exercise_code,
                                sets=sets,
                                reps_min=reps_min,
                                reps_max=reps_max,
                                rest_seconds=rest_seconds,
                            )
                            for exercise_code in blueprint[:exercise_limit]
                        )
                        days.append(
                            TemplateDayDefinition(
                                day_number=day_index,
                                title=f"Тренировка {day_index}",
                                exercises=exercises,
                            )
                        )
                    definitions.append(
                        TemplateDefinition(
                            code=_template_code(
                                goal,
                                experience_level,
                                workouts_per_week,
                                duration_bucket,
                            ),
                            name=(
                                f"{GOAL_NAMES[goal]}, "
                                f"{EXPERIENCE_NAMES[experience_level]}, "
                                f"{workouts_per_week} р./нед."
                            ),
                            goal=goal,
                            experience_level=experience_level,
                            workouts_per_week=workouts_per_week,
                            duration_bucket=duration_bucket,
                            equipment=DEFAULT_EQUIPMENT,
                            days=tuple(days),
                        )
                    )
    return tuple(definitions)


TEMPLATE_DEFINITIONS = build_template_definitions()


def _has_limitations(value: str | None) -> bool:
    return value is not None and value.strip().casefold() not in NO_LIMITATIONS_VALUES


def normalize_profile(profile: FitnessProfile) -> NormalizedProfile:
    """Validate Stage 7 profiles and preserve safe legacy defaults."""
    fallback_notes: list[str] = []

    goal = profile.goal
    if goal not in SUPPORTED_GOALS:
        goal = DEFAULT_GOAL
        fallback_notes.append(
            "Цель не распознана — использован базовый силовой шаблон."
        )

    experience = profile.experience_level
    if experience == "some_experience":
        experience = "intermediate"
    elif experience == "experienced":
        experience = "advanced"
        fallback_notes.append(
            "Legacy-уровень опыта приведён к каноническому значению."
        )
    elif experience not in SUPPORTED_EXPERIENCE:
        experience = DEFAULT_EXPERIENCE
        fallback_notes.append(
            "Уровень опыта не распознан — использован уровень новичка."
        )

    try:
        requested_workouts = int(profile.workouts_per_week)
    except (TypeError, ValueError):
        requested_workouts = 3
    training_environment = getattr(profile, "training_environment", None)
    if training_environment is None:
        training_environment = "gym"
        fallback_notes.append(
            "Место тренировок не было указано в legacy-профиле: выбран "
            "тренажёрный зал."
        )
    elif training_environment not in SUPPORTED_ENVIRONMENTS:
        training_environment = "gym"
        fallback_notes.append("Место тренировок не распознано: выбран тренажёрный зал.")
    workouts_per_week = requested_workouts
    if workouts_per_week not in SUPPORTED_FREQUENCIES:
        workouts_per_week = min(SUPPORTED_FREQUENCIES, key=lambda value: abs(value - requested_workouts))
        fallback_notes.append(
            f"Частота тренировок приведена к {workouts_per_week} р./нед."
        )

    try:
        duration = int(profile.session_duration_minutes)
    except (TypeError, ValueError):
        duration = 45
        fallback_notes.append(
            "Длительность не распознана — выбран короткий формат."
        )
    if duration not in SUPPORTED_DURATIONS:
        duration = min(SUPPORTED_DURATIONS, key=lambda value: abs(value - duration))
        fallback_notes.append(
            f"Длительность приведена к {duration} мин."
        )
    has_limitations = _has_limitations(profile.limitations)
    if has_limitations:
        fallback_notes.append(LIMITATIONS_NOTICE)
    if training_environment == "home" and goal in {"muscle_gain", "strength"}:
        fallback_notes.append(HOME_PULL_LIMITATION_NOTICE)

    return NormalizedProfile(
        goal=goal,
        experience_level=experience,
        training_environment=training_environment,
        workouts_per_week=workouts_per_week,
        session_duration_minutes=duration,
        equipment=training_environment,
        has_limitations=has_limitations,
        fallback_notes=tuple(fallback_notes),
    )


def strength_profile_constraint_message(
    *,
    goal: str,
    experience_level: str,
    training_environment: str,
) -> str | None:
    """Return a user-facing limitation when relative strength is not credible.

    This is deliberately a planning boundary, not an automatic profile edit.
    The user keeps the selected environment and can choose a more capable
    place for a particular workout instead.
    """
    if (
        goal == "strength"
        and training_environment == "home"
        and experience_level in {"intermediate", "advanced"}
    ):
        return HOME_RELATIVE_STRENGTH_LIMITATION_NOTICE
    return None


def has_credible_strength_profile(
    *,
    goal: str,
    experience_level: str,
    training_environment: str,
) -> bool:
    """Whether a controlled strength profile has an honest progression path."""
    return strength_profile_constraint_message(
        goal=goal,
        experience_level=experience_level,
        training_environment=training_environment,
    ) is None


def _profile_signature(profile: NormalizedProfile) -> str:
    payload = {
        "catalog_version": CATALOG_VERSION,
        "session_duration_minutes": profile.session_duration_minutes,
        "equipment": profile.equipment,
        "experience_level": profile.experience_level,
        "goal": profile.goal,
        "has_limitations": profile.has_limitations,
        "workouts_per_week": profile.workouts_per_week,
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _day_slots(goal: str, workouts_per_week: int, day_number: int) -> tuple[str, ...]:
    """Return deterministic movement priorities for one weekly training day."""
    full_body = (
        ("squat", "horizontal_push", "horizontal_pull", "hinge", "core"),
        ("hinge", "vertical_push", "vertical_pull", "squat", "core"),
        ("squat", "horizontal_pull", "horizontal_push", "hinge", "core"),
    )
    upper = ("horizontal_push", "horizontal_pull", "vertical_push", "vertical_pull", "isolation", "core")
    lower = ("squat", "hinge", "isolation", "squat", "core", "isolation")
    push = ("horizontal_push", "vertical_push", "isolation", "isolation", "core")
    pull = ("vertical_pull", "horizontal_pull", "hinge", "isolation", "core")
    legs = ("squat", "hinge", "squat", "isolation", "core")
    if goal == "strength" and workouts_per_week == 3:
        return (
            ("squat", "horizontal_push", "horizontal_pull", "core"),
            ("horizontal_push", "hinge", "vertical_pull", "core"),
            ("hinge", "squat", "vertical_push", "horizontal_pull", "core"),
        )[day_number - 1]
    if workouts_per_week <= 3:
        return full_body[(day_number - 1) % len(full_body)]
    if workouts_per_week == 4:
        return (upper, lower, upper, lower)[day_number - 1]
    if workouts_per_week == 5:
        return (upper, lower, push, pull, legs)[day_number - 1]
    return (push, pull, legs, push, pull, legs)[day_number - 1]


def _weekly_day_slots(
    profile: NormalizedProfile,
    day_number: int,
) -> tuple[str, ...]:
    """Return a deterministic whole-week hypertrophy layout.

    ``movement:muscle`` requests keep the taxonomy as the source of truth while
    making weekly coverage measurable.  The first three positions remain useful
    for a 30-minute session; longer sessions add balanced accessories.
    """
    if profile.goal != "muscle_gain":
        return _day_slots(profile.goal, profile.workouts_per_week, day_number)

    if profile.workouts_per_week == 2:
        days = (
            (
                "squat:quads", "horizontal_push:chest", "horizontal_pull:back",
                "hinge:glutes", "isolation:biceps", "core:core",
            ),
            (
                "hinge:hamstrings", "vertical_push:shoulders", "vertical_pull:back",
                "isolation:triceps", "squat:quads", "horizontal_push:chest",
            ),
        )
        return days[day_number - 1]

    days = (
        (
            "squat:quads", "horizontal_push:chest", "horizontal_pull:back",
            "hinge:glutes", "isolation:biceps", "isolation:calves",
        ),
        (
            "hinge:hamstrings", "vertical_push:shoulders", "isolation:biceps",
            "squat:quads", "horizontal_push:chest", "vertical_pull:back",
        ),
        (
            "vertical_pull:back", "isolation:triceps", "core:core",
            "squat:quads", "horizontal_push:chest", "hinge:glutes",
        ),
        (
            "squat:quads", "horizontal_push:chest", "horizontal_pull:back",
            "core:core", "hinge:hamstrings", "vertical_push:shoulders",
        ),
        (
            "hinge:hamstrings", "vertical_push:shoulders", "vertical_pull:back",
            "isolation:biceps", "squat:quads", "horizontal_push:chest",
        ),
        (
            "squat:quads", "horizontal_push:chest", "horizontal_pull:back",
            "isolation:triceps", "hinge:glutes", "isolation:calves",
        ),
    )
    return days[day_number - 1]


def _slot_parts(slot: str) -> tuple[str, str | None]:
    movement, separator, muscle = slot.partition(":")
    return movement, muscle if separator else None


def _exercise_priority(
    definition: ExerciseDefinition,
    profile: NormalizedProfile,
    slot: str,
    *,
    used_today: bool = False,
    weekly_use_count: int = 0,
) -> tuple[int, int, str]:
    """Sort candidates by product policy, then stable catalog code."""
    movement, _ = _slot_parts(slot)
    equipment = definition.equipment
    score = 0
    if profile.training_environment == "home" and equipment != "bodyweight":
        return (99, 99, definition.code)
    if profile.experience_level == "beginner":
        if profile.training_environment == "gym":
            score += {
                "machine": 0,
                "cable": 0,
                "smith": 2,
                "dumbbell": 5,
                "bodyweight": 6,
                "pullup_dip_station": 6,
                "functional_equipment": 7,
                "barbell": 20,
            }[equipment]
        else:
            score += 0 if equipment in {"machine", "cable", "bodyweight"} else 3
        if equipment == "barbell" and movement in {"squat", "hinge"}:
            score += 10
        if (
            profile.training_environment == "gym"
            and movement == "squat"
            and definition.code == "bodyweight_squat"
        ):
            score += 20
        if definition.code == "bench_dip":
            # When cables or machines exist, a novice should not be routed to
            # the less stable bench-dip variant as a default triceps choice.
            score += 30
    elif profile.experience_level == "advanced":
        score += 0 if equipment in {"barbell", "dumbbell", "bodyweight"} else 2
    else:
        score += 0 if equipment in {"dumbbell", "barbell", "cable", "bodyweight"} else 1
    if profile.goal == "strength" and movement in {
        "squat", "hinge", "horizontal_push",
    }:
        score += 0 if equipment == "barbell" and profile.experience_level != "beginner" else 2
    if definition.movement_pattern == "locomotion_conditioning":
        score += 8
    if weekly_use_count:
        score += weekly_use_count * 5
    if used_today:
        score += 100
    return (score, 0 if definition.primary_muscle_group not in {"biceps", "triceps"} else 1, definition.code)


def _choose_exercise(
    profile: NormalizedProfile,
    slot: str,
    used_codes: set[str],
    weekly_use_counts: dict[str, int] | None = None,
    *,
    allow_conventional_deadlift: bool = False,
) -> ExerciseDefinition | None:
    if slot.startswith("code:"):
        definition = exercise_definition_by_code(slot.removeprefix("code:"))
        if (
            definition is None
            or (
                definition.code == "barbell_deadlift"
                and not allow_conventional_deadlift
            )
            or definition.code in used_codes
            or profile.training_environment not in definition.environments
            or profile.experience_level not in definition.experience_levels
            or (
                profile.training_environment == "home"
                and definition.equipment != "bodyweight"
            )
        ):
            return None
        return definition
    movement, primary_muscle = _slot_parts(slot)
    weekly_use_counts = weekly_use_counts or {}

    def compatible(definition: ExerciseDefinition, requested_movement: str) -> bool:
        return (
            profile.training_environment in definition.environments
            and profile.experience_level in definition.experience_levels
            and definition.movement_pattern == requested_movement
            and (
                primary_muscle is None
                or definition.primary_muscle_group == primary_muscle
            )
            and definition.progression_type
            in {"external_load_reps", "bodyweight_reps"}
            and not (
                profile.goal == "strength"
                and profile.training_environment in {"home", "street"}
                and definition.progression_type != "bodyweight_reps"
            )
            and not (
                profile.training_environment == "home"
                and definition.equipment != "bodyweight"
            )
            and (
                allow_conventional_deadlift
                or definition.code != "barbell_deadlift"
            )
        )

    candidates = [
        definition
        for definition in EXERCISE_DEFINITIONS
        if compatible(definition, movement)
        and definition.code not in used_codes
    ]
    if not candidates and movement == "vertical_push":
        candidates = [
            definition
            for definition in EXERCISE_DEFINITIONS
            if compatible(definition, "horizontal_push")
            and definition.code not in used_codes
        ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda definition: _exercise_priority(
            definition,
            profile,
            slot,
            used_today=definition.code in used_codes,
            weekly_use_count=weekly_use_counts.get(definition.code, 0),
        ),
    )


def _working_set_target(profile: NormalizedProfile, *, is_main: bool) -> int:
    """Keep longer sessions useful without turning high frequency into junk volume."""
    by_duration = {30: 2, 45: 3, 60: 3, 90: 4}
    target = by_duration[profile.session_duration_minutes]
    if (
        not is_main
        and profile.session_duration_minutes == 45
        and (
            profile.goal == "strength"
            or (
                profile.goal == "fat_loss"
                and profile.training_environment == "functional_gym"
            )
        )
    ):
        # A compact support prescription keeps a genuine 45-minute session
        # inside its promise without simply deleting a duration option.
        target = 2
    if (
        not is_main
        and profile.goal == "strength"
        and profile.experience_level == "beginner"
        and profile.workouts_per_week >= 3
        and profile.session_duration_minutes <= 60
    ):
        target = min(target, 2)
    if profile.workouts_per_week >= 5:
        target = min(target, 2)
    elif profile.workouts_per_week == 4:
        target = min(target, 3)
    if not is_main and profile.session_duration_minutes != 90:
        target = min(target, 3)
    return target


def _prescription(
    profile: NormalizedProfile,
    definition: ExerciseDefinition,
    position: int,
    *,
    is_main: bool,
) -> tuple[int, int, int, int]:
    bodyweight = definition.progression_type == "bodyweight_reps"
    if profile.goal == "strength":
        if profile.training_environment in {"home", "street"}:
            level = profile.experience_level
            return (
                _working_set_target(profile, is_main=is_main),
                {"beginner": 6, "intermediate": 5, "advanced": 5}[level]
                if is_main else 7,
                {"beginner": 12, "intermediate": 10, "advanced": 8}[level]
                if is_main else {"beginner": 14, "intermediate": 12, "advanced": 10}[level],
                90 if is_main else 60,
            )
        if is_main and definition.movement_pattern in {"squat", "hinge", "horizontal_push"}:
            main_sets = {
                "beginner": 3,
                "intermediate": 3,
                "advanced": 4,
            }[profile.experience_level]
            if profile.session_duration_minutes == 30 or profile.workouts_per_week >= 5:
                main_sets = min(main_sets, 3)
            if profile.session_duration_minutes == 30 and profile.workouts_per_week >= 5:
                main_sets = min(main_sets, 2)
            if profile.experience_level == "beginner":
                return main_sets, 5, 8, 120
            if profile.experience_level == "advanced":
                rest = {
                    30: 120,
                    45: 120,
                    60: 150,
                    90: 180,
                }[profile.session_duration_minutes]
                return main_sets, 3, 5, rest
            return main_sets, 4, 6, 120 if profile.session_duration_minutes <= 45 else 150
        return (
            _working_set_target(profile, is_main=is_main),
            6 if is_main else 8,
            10 if is_main else 12,
            90 if is_main else 60,
        )
    sets = _working_set_target(profile, is_main=is_main)
    if profile.goal == "fat_loss":
        level_bonus = {"beginner": 0, "intermediate": 1, "advanced": 2}[profile.experience_level]
        return (
            sets,
            8 if is_main else 10 + level_bonus,
            12 + level_bonus if is_main else 15 + level_bonus,
            75 if is_main else 45,
        )
    if bodyweight:
        level_bonus = {"beginner": 0, "intermediate": 1, "advanced": 2}[profile.experience_level]
        return (
            sets,
            8 if is_main else 10 + level_bonus,
            15 + level_bonus if is_main else 18 + level_bonus,
            75 if is_main else 45,
        )
    level_bonus = {"beginner": 0, "intermediate": 1, "advanced": 2}[profile.experience_level]
    return (
        sets,
        6 if is_main else 8 + level_bonus,
        12 + level_bonus if is_main else 15 + level_bonus,
        90 if is_main else 60,
    )


def _progression_strategy(
    profile: NormalizedProfile,
    definition: ExerciseDefinition,
    position: int,
    *,
    is_main: bool,
) -> ProgressionStrategy:
    """Route a persisted prescription by goal, exercise role and capability."""
    if definition.progression_type == "bodyweight_reps":
        return ProgressionStrategy.BODYWEIGHT_REPS
    if definition.progression_type != "external_load_reps":
        raise WorkoutCatalogError(
            f"Unsupported progression capability: {definition.progression_type}"
        )
    if (
        profile.goal == "strength"
        and is_main
        and definition.movement_pattern in {"squat", "hinge", "horizontal_push"}
    ):
        return ProgressionStrategy.STRENGTH_LOAD_REPS
    return ProgressionStrategy.HYPERTROPHY_LOAD_REPS


def _functional_block(
    profile: NormalizedProfile,
    day_number: int,
    *,
    excluded_codes: set[str] | None = None,
) -> GeneratedBlockDefinition | None:
    """Route a conservative conditioning block without replacing resistance work."""
    environment = profile.training_environment
    if environment not in {"functional_gym", "street"}:
        return None
    if environment == "street":
        frequency = {"strength": 0, "muscle_gain": 1, "fat_loss": 2}[profile.goal]
        if frequency == 0 or day_number > min(profile.workouts_per_week, frequency):
            return None
        workout_format = "circuit_rounds"
    else:
        frequency = {"strength": 1, "muscle_gain": 1, "fat_loss": 3}[profile.goal]
        if day_number > min(profile.workouts_per_week, frequency):
            return None
        formats = {
            "strength": ("emom",),
            "muscle_gain": ("amrap",),
            "fat_loss": ("amrap", "emom", "for_time"),
        }[profile.goal]
        workout_format = formats[(day_number - 1) % len(formats)]

    excluded_codes = excluded_codes or set()
    candidates = [
        item for item in EXERCISE_DEFINITIONS
        if environment in item.environments
        and profile.experience_level in item.experience_levels
        and item.equipment in (
            {"bodyweight", "pullup_dip_station"}
            if environment == "street"
            else {"bodyweight", "functional_equipment"}
        )
        and item.progression_type in {"bodyweight_reps", "timed_conditioning"}
        and item.movement_pattern in {
            "squat", "hinge", "horizontal_push", "horizontal_pull", "vertical_pull",
            "core", "locomotion_conditioning",
        }
        and item.code not in {
            "barbell_back_squat", "barbell_bench_press", "barbell_deadlift",
        }
        and item.code not in excluded_codes
    ]
    candidates.sort(key=lambda item: (
        1 if item.progression_type == "timed_conditioning" else 0,
        item.movement_pattern,
        item.code,
    ))
    selected: list[ExerciseDefinition] = []
    patterns: set[str] = set()
    for item in candidates:
        if item.movement_pattern in patterns:
            continue
        selected.append(item)
        patterns.add(item.movement_pattern)
        if len(selected) == (2 if profile.experience_level == "beginner" else 3):
            break
    if not selected:
        raise WorkoutCatalogError("Functional block has no compatible exercises.")

    # A 45-minute session keeps a compact but real conditioning finish.  Five
    # minutes pushed several otherwise sound resistance sessions past the
    # truthful upper bound, creating an artificial 30/60 duration hole.
    duration_minutes = {30: 3, 45: 3, 60: 8, 90: 12}[profile.session_duration_minutes]
    if profile.experience_level == "beginner":
        duration_minutes = max(3, duration_minutes - 2)
    target_rounds = None
    if workout_format in {"for_time", "circuit_rounds"}:
        # A 45-minute outdoor circuit uses two quality rounds.  A third round
        # made the compact session exceed its declared time instead of adding
        # useful strength or conditioning work.
        target_rounds = {30: 2, 45: 2, 60: 3, 90: 4}[profile.session_duration_minutes]
    reps = 6 if profile.experience_level == "beginner" else 8
    return GeneratedBlockDefinition(
        title="Функциональный тренинг" if environment == "functional_gym" else "Круговая тренировка",
        workout_format=workout_format,
        duration_seconds=duration_minutes * 60 if workout_format in {"amrap", "emom"} else None,
        target_rounds=target_rounds,
        exercises=tuple(
            GeneratedFormatExerciseDefinition(item.code, reps + index * 2)
            for index, item in enumerate(selected)
        ),
    )


def program_archetype(profile: NormalizedProfile) -> str:
    """Name the training logic before individual exercise selection begins."""
    if profile.goal == "muscle_gain":
        return {
            2: "Всё тело A/B",
            3: "Всё тело A/B/C",
            4: "Верх/низ тела A/B",
            5: "Верх/низ/жим/тяга/ноги",
            6: "Жим/тяга/ноги A/B",
        }[profile.workouts_per_week]
    if profile.goal == "strength":
        return (
            "Относительная сила с собственным весом"
            if profile.training_environment in {"home", "street"}
            else "Силовая база: присед / жим / тяга"
        )
    return "Силовая база с дозированным кондиционированием"


def _muscle_gain_blueprints(frequency: int) -> tuple[ProgramDayBlueprint, ...]:
    full_body = (
        ProgramDayBlueprint(
            "Всё тело A",
            ("squat:quads", "horizontal_push:chest", "horizontal_pull:back", "hinge:glutes", "isolation:biceps", "core:core"),
        ),
        ProgramDayBlueprint(
            "Всё тело B",
            ("hinge:hamstrings", "vertical_push:shoulders", "vertical_pull:back", "lunge:quads", "isolation:triceps", "isolation:calves"),
        ),
        ProgramDayBlueprint(
            "Всё тело C",
            ("lunge:quads", "horizontal_push:chest", "vertical_pull:back", "isolation:triceps", "core:core", "isolation:biceps"),
        ),
    )
    if frequency == 2:
        return full_body[:2]
    if frequency == 3:
        return full_body
    if frequency == 4:
        return (
            ProgramDayBlueprint("Верх тела A", ("horizontal_push:chest", "horizontal_pull:back", "vertical_push:shoulders", "vertical_pull:back", "isolation:biceps", "isolation:triceps")),
            ProgramDayBlueprint("Низ тела A", ("squat:quads", "hinge:hamstrings", "isolation:calves", "lunge:quads", "core:core")),
            ProgramDayBlueprint("Верх тела B", ("horizontal_push:chest", "vertical_pull:back", "vertical_push:shoulders", "horizontal_pull:back", "isolation:triceps", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Низ тела B", ("hinge:glutes", "squat:quads", "isolation:calves", "lunge:quads", "core:core")),
        )
    if frequency == 5:
        return (
            ProgramDayBlueprint("Верх тела", ("horizontal_push:chest", "horizontal_pull:back", "vertical_push:shoulders", "vertical_pull:back", "isolation:biceps", "isolation:triceps")),
            ProgramDayBlueprint("Низ тела", ("squat:quads", "hinge:hamstrings", "isolation:calves", "lunge:quads", "core:core")),
            ProgramDayBlueprint("Жимовой день", ("horizontal_push:chest", "vertical_push:shoulders", "isolation:triceps", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Тяговый день", ("vertical_pull:back", "horizontal_pull:back", "isolation:biceps", "hinge:glutes", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Ноги", ("squat:quads", "hinge:hamstrings", "isolation:calves", "lunge:quads", "core:core")),
        )
    return (
        ProgramDayBlueprint("Жимовой день A", ("horizontal_push:chest", "vertical_push:shoulders", "isolation:triceps", "scapular_rear_delt:shoulders")),
        ProgramDayBlueprint("Тяговый день A", ("vertical_pull:back", "horizontal_pull:back", "isolation:biceps", "hinge:glutes", "scapular_rear_delt:shoulders")),
        ProgramDayBlueprint("Ноги A", ("squat:quads", "hinge:hamstrings", "isolation:calves", "lunge:quads", "core:core")),
        ProgramDayBlueprint("Жимовой день B", ("horizontal_push:chest", "vertical_push:shoulders", "isolation:triceps", "scapular_rear_delt:shoulders")),
        ProgramDayBlueprint("Тяговый день B", ("horizontal_pull:back", "vertical_pull:back", "isolation:biceps", "hinge:glutes", "core:core")),
        ProgramDayBlueprint("Ноги B", ("hinge:hamstrings", "squat:quads", "lunge:quads", "isolation:calves")),
    )


def _strength_blueprints(profile: NormalizedProfile) -> tuple[ProgramDayBlueprint, ...]:
    if profile.training_environment in {"home", "street"}:
        if profile.training_environment == "home":
            # Home deliberately does not pretend that scapular drills are pull
            # movements.  Its controlled beginner ladder has a genuine squat
            # successor and the same no-equipment boundary is enforced before
            # intermediate/advanced plans are allowed to exist.
            relative = (
                ProgramDayBlueprint("Относительная сила A", ("code:bodyweight_squat", "code:push_up", "code:bodyweight_glute_bridge", "lunge:quads", "core:core", "isolation:calves")),
                ProgramDayBlueprint("Относительная сила B", ("code:bodyweight_squat", "code:push_up", "lunge:quads", "hinge:glutes", "scapular_rear_delt:shoulders", "isolation:calves")),
                ProgramDayBlueprint("Относительная сила C", ("code:bodyweight_squat", "code:push_up", "code:bodyweight_glute_bridge", "lunge:quads", "scapular_rear_delt:shoulders", "core:core")),
            )
        else:
            if profile.experience_level == "beginner":
                relative = (
                    ProgramDayBlueprint("Относительная сила A", ("code:bodyweight_squat", "code:incline_push_up", "code:inverted_row", "hinge:glutes", "core:core")),
                    ProgramDayBlueprint("Относительная сила B", ("hinge:glutes", "code:incline_push_up", "code:inverted_row", "lunge:quads", "isolation:calves")),
                    ProgramDayBlueprint("Относительная сила C", ("code:bodyweight_squat", "code:push_up", "code:inverted_row", "hinge:hamstrings", "scapular_rear_delt:shoulders")),
                )
            else:
                # Street has a real, controlled ladder: unilateral legs,
                # single-leg hinge, pull-up/chin-up and dip progressions.  The
                # selected code slots prevent a lexicographic fallback to easy
                # bodyweight squat or push-up as an advanced main movement.
                pull = "code:chin_up" if profile.experience_level == "advanced" else "code:pull_up"
                relative = (
                    ProgramDayBlueprint("Относительная сила A", ("code:reverse_lunge", "code:parallel_bar_dip", pull, "code:single_leg_glute_bridge", "core:core")),
                    ProgramDayBlueprint("Относительная сила B", ("code:single_leg_glute_bridge", "code:parallel_bar_dip", "code:inverted_row", "code:reverse_lunge", "isolation:calves")),
                    ProgramDayBlueprint("Относительная сила C", ("code:reverse_lunge", "code:parallel_bar_dip", pull, "code:single_leg_glute_bridge", "scapular_rear_delt:shoulders")),
                )
        return tuple(relative[index % len(relative)] for index in range(profile.workouts_per_week))

    if profile.experience_level == "beginner":
        if profile.training_environment == "gym":
            base = (
                ProgramDayBlueprint("Присед и жим", ("code:leg_press", "code:chest_press", "horizontal_pull:back", "core:core")),
                ProgramDayBlueprint("Тяга и жим", ("hinge:glutes", "code:chest_press", "vertical_pull:back", "isolation:triceps")),
                ProgramDayBlueprint("Ноги и техника", ("squat:quads", "hinge:hamstrings", "horizontal_pull:back", "core:core")),
            )
        else:
            base = (
                ProgramDayBlueprint("Присед и жим", ("code:dumbbell_goblet_squat", "code:dumbbell_bench_press", "horizontal_pull:back", "core:core")),
                ProgramDayBlueprint("Тяга и жим", ("hinge:glutes", "code:dumbbell_bench_press", "vertical_pull:back", "isolation:triceps")),
                ProgramDayBlueprint("Ноги и техника", ("squat:quads", "hinge:hamstrings", "horizontal_pull:back", "core:core")),
            )
    elif profile.experience_level == "intermediate":
        # Intermediate trainees use loadable S/B/D-supporting movements, but
        # never an advanced-only conventional deadlift.  The two-day version
        # therefore remains a real 45/60-minute plan rather than a shortened
        # day caused by a silently unavailable main lift.
        base = (
            ProgramDayBlueprint("Присед и жим", ("code:barbell_back_squat", "code:barbell_bench_press", "horizontal_pull:back", "vertical_pull:back", "core:core", "isolation:calves")),
            ProgramDayBlueprint("Тяга и поддержка", ("code:barbell_romanian_deadlift", "vertical_pull:back", "horizontal_push:chest", "lunge:quads", "isolation:triceps", "core:core")),
            ProgramDayBlueprint("Жим", ("code:barbell_bench_press", "horizontal_pull:back", "vertical_push:shoulders", "lunge:quads", "isolation:triceps", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Присед и поддержка", ("code:barbell_back_squat", "vertical_pull:back", "horizontal_push:chest", "isolation:calves", "core:core")),
            ProgramDayBlueprint("Жим и верх тела", ("code:barbell_bench_press", "horizontal_pull:back", "vertical_push:shoulders", "isolation:triceps", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Восстановительная силовая база", ("lunge:quads", "horizontal_pull:back", "vertical_pull:back", "scapular_rear_delt:shoulders", "core:core")),
        )
    else:
        # S/B/D exposure is intentionally uneven: bench tolerates more useful
        # practice, squat is moderate, and the conventional deadlift is kept to
        # one heavy exposure per week.  It is never an accessory prescription.
        base = (
            ProgramDayBlueprint("Присед и жим", ("code:barbell_back_squat", "code:barbell_bench_press", "horizontal_pull:back", "core:core", "isolation:calves")),
            ProgramDayBlueprint("Тяга", ("code:barbell_deadlift", "vertical_pull:back", "horizontal_push:chest", "isolation:triceps", "core:core")),
            ProgramDayBlueprint("Жим", ("code:barbell_bench_press", "horizontal_pull:back", "vertical_push:shoulders", "lunge:quads", "isolation:triceps")),
            ProgramDayBlueprint("Присед и поддержка", ("code:barbell_back_squat", "vertical_pull:back", "horizontal_push:chest", "isolation:calves", "core:core")),
            ProgramDayBlueprint("Жим и верх тела", ("code:barbell_bench_press", "horizontal_pull:back", "vertical_push:shoulders", "isolation:triceps", "scapular_rear_delt:shoulders")),
            ProgramDayBlueprint("Восстановительная силовая база", ("lunge:quads", "horizontal_pull:back", "vertical_pull:back", "scapular_rear_delt:shoulders", "core:core")),
        )
    return tuple(
        base[index] if len(base) == 6 else base[index % len(base)]
        for index in range(profile.workouts_per_week)
    )


def _home_resistance_blueprints(frequency: int) -> tuple[ProgramDayBlueprint, ...]:
    """Honest no-equipment home sessions, without a fictional pull substitute."""
    base = (
        ProgramDayBlueprint("Всё тело A", ("squat:quads", "horizontal_push:chest", "hinge:glutes", "lunge:quads", "isolation:calves", "core:core")),
        ProgramDayBlueprint("Всё тело B", ("lunge:quads", "horizontal_push:chest", "hinge:hamstrings", "scapular_rear_delt:shoulders", "core:core", "isolation:calves")),
        ProgramDayBlueprint("Всё тело C", ("squat:quads", "vertical_push:shoulders", "hinge:glutes", "lunge:quads", "scapular_rear_delt:shoulders", "core:core")),
    )
    return tuple(base[index % len(base)] for index in range(frequency))


def _purposeful_fill_slots(profile: NormalizedProfile) -> tuple[str, ...]:
    """Fill a genuinely missing movement slot, never a fake pull or junk set."""
    if profile.training_environment == "home":
        return (
            "lunge:quads", "hinge:glutes", "core:core", "isolation:calves",
            "scapular_rear_delt:shoulders", "horizontal_push:chest",
        )
    if profile.training_environment == "street":
        return (
            "lunge:quads", "hinge:glutes", "core:core", "isolation:calves",
            "vertical_pull:back", "horizontal_push:chest",
        )
    if profile.training_environment in {"gym", "functional_gym"}:
        # A longer session may add only complementary work that supports the
        # main pattern already chosen for that day.  This is capacity scaling,
        # not generic filler: exact duplicates are rejected by ``used_codes``
        # and the weekly recovery gate remains authoritative.
        return (
            "horizontal_pull:back", "vertical_pull:back", "lunge:quads",
            "hinge:glutes", "scapular_rear_delt:shoulders", "isolation:calves",
            "core:core", "isolation:triceps",
        )
    return ()


def _program_blueprints(profile: NormalizedProfile) -> tuple[ProgramDayBlueprint, ...]:
    if profile.goal == "muscle_gain":
        if profile.training_environment == "home":
            return _home_resistance_blueprints(profile.workouts_per_week)
        return _muscle_gain_blueprints(profile.workouts_per_week)
    if profile.goal == "strength":
        return _strength_blueprints(profile)
    resistance = (
        _home_resistance_blueprints(profile.workouts_per_week)
        if profile.training_environment == "home"
        else _muscle_gain_blueprints(profile.workouts_per_week)
    )
    return tuple(
        ProgramDayBlueprint(
            title=f"Силовая база — {day.title}",
            slots=day.slots,
            include_conditioning=profile.training_environment in {"functional_gym", "street"},
        )
        for day in resistance
    )


def estimate_generated_day_minutes(
    profile: NormalizedProfile,
    day: GeneratedDayDefinition,
) -> int:
    """Estimate required preparation and quality work without optional cooldown."""
    warmup = build_generated_day_warmup(profile, day)
    # Only time attached to actual preparation, work, prescribed rest and
    # equipment transitions is counted.  Optional cooldown is excluded.
    minutes = float(warmup.estimated_minutes)
    for position, exercise in enumerate(day.exercises):
        working_minutes = exercise.sets * (1.5 if position == 0 else 1.2)
        rest_minutes = max(0, exercise.sets - 1) * exercise.rest_seconds / 60
        # This is the actual station/load/technique transition, not a generic
        # duration filler.  Free-weight and bodyweight set-up is still present.
        setup_minutes = 4.5 if position == 0 else 2.25
        minutes += working_minutes + rest_minutes + setup_minutes
    for block in day.blocks:
        minutes += 2
        if block.duration_seconds:
            minutes += block.duration_seconds / 60
        elif block.target_rounds:
            # A round is a compact secondary conditioning component, not an
            # invented three-minute filler after every resistance session.
            minutes += block.target_rounds * 2.5
    return int(math.ceil(minutes))


def _generate_program_unchecked(profile: NormalizedProfile) -> GeneratedProgramDefinition:
    """Build a deterministic program before duration/recovery quality gates."""
    days: list[GeneratedDayDefinition] = []
    budget = DURATION_EXERCISE_BUDGETS[profile.session_duration_minutes]
    weekly_use_counts: dict[str, int] = {}
    # Core work is useful, but it is not a mandatory daily filler.  Keep it
    # deliberately below the weekly training frequency so a two-day plan has
    # one focused exposure and longer weeks have at most two.
    core_day_limit = min(2, max(0, profile.workouts_per_week - 1))
    core_days_assigned = 0
    for day_number, blueprint in enumerate(_program_blueprints(profile), start=1):
        used_codes: set[str] = set()
        exercises: list[GeneratedExerciseDefinition] = []
        for slot in blueprint.slots:
            if len(exercises) >= budget:
                break
            movement, _ = _slot_parts(slot)
            if movement == "core" and core_days_assigned >= core_day_limit:
                continue
            is_main = not exercises
            definition = _choose_exercise(
                profile,
                slot,
                used_codes,
                weekly_use_counts,
                allow_conventional_deadlift=(
                    is_main and slot == "code:barbell_deadlift"
                ),
            )
            # Home deliberately skips unavailable true pulling slots instead of
            # relabelling scapular accessories as a pull substitute.
            if definition is None:
                continue
            used_codes.add(definition.code)
            weekly_use_counts[definition.code] = weekly_use_counts.get(definition.code, 0) + 1
            sets, reps_min, reps_max, rest_seconds = _prescription(
                profile,
                definition,
                len(exercises),
                is_main=is_main,
            )
            exercises.append(
                GeneratedExerciseDefinition(
                    definition.code,
                    sets,
                    reps_min,
                    reps_max,
                    rest_seconds,
                    _progression_strategy(
                        profile,
                        definition,
                        len(exercises),
                        is_main=is_main,
                    ),
                )
            )
            if definition.primary_muscle_group == "core":
                core_days_assigned += 1
        # Street and home have intentionally fewer isolated exercise families.
        # If an unavailable slot left a short day incomplete, add only a
        # complementary controlled movement from the same real environment.
        for slot in _purposeful_fill_slots(profile):
            if len(exercises) >= budget:
                break
            movement, _ = _slot_parts(slot)
            if movement == "core" and core_days_assigned >= core_day_limit:
                continue
            definition = _choose_exercise(
                profile,
                slot,
                used_codes,
                weekly_use_counts,
            )
            if definition is None:
                continue
            used_codes.add(definition.code)
            weekly_use_counts[definition.code] = weekly_use_counts.get(definition.code, 0) + 1
            sets, reps_min, reps_max, rest_seconds = _prescription(
                profile,
                definition,
                len(exercises),
                is_main=not exercises,
            )
            exercises.append(
                GeneratedExerciseDefinition(
                    definition.code,
                    sets,
                    reps_min,
                    reps_max,
                    rest_seconds,
                    _progression_strategy(
                        profile,
                        definition,
                        len(exercises),
                        is_main=False,
                    ),
                )
            )
            if definition.primary_muscle_group == "core":
                core_days_assigned += 1
        if not exercises:
            raise WorkoutCatalogError("Generated workout day is empty.")
        block = (
            _functional_block(
                profile,
                day_number,
                excluded_codes=used_codes,
            )
            if blueprint.include_conditioning
            else None
        )
        days.append(GeneratedDayDefinition(
            day_number=day_number,
            title=blueprint.title,
            exercises=tuple(exercises),
            blocks=(block,) if block is not None else (),
        ))
    return GeneratedProgramDefinition(
        name=(
            f"{GOAL_NAMES[profile.goal]} — {program_archetype(profile)}; "
            f"{EXPERIENCE_NAMES[profile.experience_level]}, "
            f"{profile.training_environment}, {profile.workouts_per_week} р./нед."
        ),
        days=tuple(days),
    )


def _weekly_working_set_limit(profile: NormalizedProfile) -> int:
    """A conservative whole-week ceiling, not a claim of universal optimality."""
    if profile.goal == "strength":
        return {
            "beginner": 36,
            "intermediate": 50,
            "advanced": 60,
        }[profile.experience_level]
    return {
        "beginner": 48,
        "intermediate": 60,
        "advanced": 72,
    }[profile.experience_level]


def _duration_fits(profile: NormalizedProfile, day: GeneratedDayDefinition) -> bool:
    minutes = estimate_generated_day_minutes(profile, day)
    return (
        math.ceil(profile.session_duration_minutes * DURATION_FIT_MIN_RATIO)
        <= minutes
        <= math.floor(profile.session_duration_minutes * DURATION_FIT_MAX_RATIO)
    )


def _relative_strength_progression_failures(
    profile: NormalizedProfile,
    program: GeneratedProgramDefinition,
) -> tuple[str, ...]:
    """Reject easy-only intermediate/advanced bodyweight strength plans."""
    if (
        profile.goal != "strength"
        or profile.training_environment not in {"home", "street"}
    ):
        return ()
    constraint = strength_profile_constraint_message(
        goal=profile.goal,
        experience_level=profile.experience_level,
        training_environment=profile.training_environment,
    )
    if constraint is not None:
        return ("relative-strength profile has no credible catalog ladder",)
    if profile.experience_level == "beginner":
        return ()

    failures: list[str] = []
    easy_main_codes = {
        "bodyweight_squat",
        "push_up",
        "incline_push_up",
        "bodyweight_glute_bridge",
    }
    for day in program.days:
        if not day.exercises or day.exercises[0].exercise_code in easy_main_codes:
            failures.append(
                f"day-{day.day_number}: easy-only relative-strength main movement"
            )

    weekly_codes = {
        item.exercise_code for day in program.days for item in day.exercises
    }
    ladder = set(RELATIVE_STRENGTH_LADDERS[
        profile.training_environment
    ][profile.experience_level])
    if not ladder <= weekly_codes:
        failures.append("relative-strength ladder is incomplete")
    return tuple(failures)


def validate_generated_program_quality(
    profile: NormalizedProfile,
    program: GeneratedProgramDefinition,
    *,
    include_duration: bool = True,
) -> tuple[str, ...]:
    """Return deterministic quality-gate failures for a generated week."""
    failures: list[str] = []
    weekly_sets = 0
    specific_exposure: dict[str, int] = {
        "barbell_back_squat": 0,
        "barbell_bench_press": 0,
        "barbell_deadlift": 0,
    }
    high_stress_days: list[int] = []
    for day in program.days:
        standard_sets = sum(item.sets for item in day.exercises)
        weekly_sets += standard_sets
        has_timed_block = bool(day.blocks)
        minimum_sets = {30: 5, 45: 6, 60: 8, 90: 10}[profile.session_duration_minutes]
        minimum_exercises = 2 if profile.session_duration_minutes == 30 else 3
        if profile.session_duration_minutes >= 90:
            minimum_exercises = 4
        if (not has_timed_block and len(day.exercises) < minimum_exercises) or (
            standard_sets < minimum_sets
        ):
            failures.append(f"day-{day.day_number}: minimum viable session")
        if include_duration and not _duration_fits(profile, day):
            failures.append(f"day-{day.day_number}: duration outside truthful range")

        deadlift_is_heavy = False
        for position, item in enumerate(day.exercises):
            if item.exercise_code in specific_exposure:
                specific_exposure[item.exercise_code] += 1
            if item.exercise_code == "barbell_deadlift":
                if position != 0 or item.reps_max > 6:
                    failures.append(
                        f"day-{day.day_number}: conventional deadlift is not a heavy main lift"
                    )
                deadlift_is_heavy = item.reps_max <= 6
        if deadlift_is_heavy:
            high_stress_days.append(day.day_number)

    if weekly_sets > _weekly_working_set_limit(profile):
        failures.append("weekly working-set recovery limit")
    if profile.goal == "strength" and profile.training_environment in {"gym", "functional_gym"}:
        caps = {
            "barbell_back_squat": 2,
            "barbell_bench_press": 3,
            "barbell_deadlift": 1,
        }
        for code, maximum in caps.items():
            if specific_exposure[code] > maximum:
                failures.append(f"weekly S/B/D exposure: {code}")
    if any(
        later == earlier + 1
        for earlier, later in zip(high_stress_days, high_stress_days[1:])
    ):
        failures.append("consecutive high-stress hinge days")
    failures.extend(_relative_strength_progression_failures(profile, program))
    return tuple(failures)


@lru_cache(maxsize=None)
def supported_session_durations(
    *,
    goal: str,
    experience_level: str,
    training_environment: str,
    workouts_per_week: int,
) -> tuple[int, ...]:
    """Expose a truthful, user-comprehensible contiguous duration prefix."""
    if not has_credible_strength_profile(
        goal=goal,
        experience_level=experience_level,
        training_environment=training_environment,
    ):
        return ()
    supported: list[int] = []
    for duration in sorted(SUPPORTED_DURATIONS):
        # With no equipment, a 90-minute home session would require either
        # fictional equipment or endurance-padding.  Keep that limitation
        # explicit instead of silently altering the user's request.
        if training_environment == "home" and duration == 90:
            continue
        candidate = NormalizedProfile(
            goal=goal,
            experience_level=experience_level,
            training_environment=training_environment,
            workouts_per_week=workouts_per_week,
            session_duration_minutes=duration,
            equipment=training_environment,
            has_limitations=False,
            fallback_notes=(),
        )
        try:
            program = _generate_program_unchecked(candidate)
        except WorkoutCatalogError:
            break
        if validate_generated_program_quality(candidate, program):
            # Product-facing duration choices must never have gaps.  If a
            # shorter option cannot be built honestly, a longer one is not a
            # meaningful option even if a coincidental estimate happened to fit.
            break
        supported.append(duration)
    return tuple(supported)


def generate_program(profile: NormalizedProfile) -> GeneratedProgramDefinition:
    """Build a valid deterministic program or fail before a misleading plan exists."""
    constraint = strength_profile_constraint_message(
        goal=profile.goal,
        experience_level=profile.experience_level,
        training_environment=profile.training_environment,
    )
    if constraint is not None:
        raise WorkoutStrengthProfileUnsupportedError(constraint)
    supported = supported_session_durations(
        goal=profile.goal,
        experience_level=profile.experience_level,
        training_environment=profile.training_environment,
        workouts_per_week=profile.workouts_per_week,
    )
    if profile.session_duration_minutes not in supported:
        choices = ", ".join(f"{value} мин" for value in supported)
        raise WorkoutDurationUnsupportedError(
            "Для выбранных условий эта длительность не даёт качественную "
            f"тренировку. Доступные варианты: {choices or 'нет'}."
        )
    program = _generate_program_unchecked(profile)
    failures = validate_generated_program_quality(profile, program)
    if failures:
        raise WorkoutCatalogError("Generated program quality gate: " + "; ".join(failures))
    return program


def _ensure_catalog_in_session(session: Session) -> CatalogStats:
    exercises_by_code: dict[str, Exercise] = {}
    for definition in EXERCISE_DEFINITIONS:
        try:
            validate_exercise_definition(definition)
        except ValueError as error:
            raise WorkoutCatalogError(str(error)) from error
        exercise = session.scalar(
            select(Exercise).where(Exercise.code == definition.code)
        )
        tags = ",".join(definition.restriction_tags)
        secondary_groups = ",".join(definition.secondary_muscle_groups)
        environments = ",".join(definition.environments)
        experience_levels = ",".join(definition.experience_levels)
        if exercise is None:
            exercise = Exercise(
                code=definition.code,
                name=definition.name,
                muscle_group=definition.muscle_group,
                primary_muscle_group=definition.primary_muscle_label,
                equipment=definition.equipment,
                variant=definition.variant,
                alternative_name=definition.alternative_name,
                hint=definition.hint,
                restriction_tags=tags,
                secondary_muscle_groups=secondary_groups,
                training_environments=environments,
                experience_levels=experience_levels,
                movement_pattern=definition.movement_pattern,
                progression_type=definition.progression_type,
                equivalence_group=definition.equivalence_group,
            )
            session.add(exercise)
            session.flush()
        else:
            exercise.name = definition.name
            exercise.muscle_group = definition.muscle_group
            exercise.primary_muscle_group = definition.primary_muscle_label
            exercise.equipment = definition.equipment
            exercise.variant = definition.variant
            exercise.alternative_name = definition.alternative_name
            exercise.hint = definition.hint
            exercise.restriction_tags = tags
            exercise.secondary_muscle_groups = secondary_groups
            exercise.training_environments = environments
            exercise.experience_levels = experience_levels
            exercise.movement_pattern = definition.movement_pattern
            exercise.progression_type = definition.progression_type
            exercise.equivalence_group = definition.equivalence_group
        exercises_by_code[definition.code] = exercise

    for definition in TEMPLATE_DEFINITIONS:
        existing = session.scalar(
            select(WorkoutTemplate).where(
                WorkoutTemplate.code == definition.code
            )
        )
        if existing is not None:
            continue

        template = WorkoutTemplate(
            code=definition.code,
            name=definition.name,
            goal=definition.goal,
            experience_level=definition.experience_level,
            workouts_per_week=definition.workouts_per_week,
            duration_bucket=definition.duration_bucket,
            equipment=definition.equipment,
        )
        session.add(template)
        session.flush()
        for day_definition in definition.days:
            day = WorkoutTemplateDay(
                template_id=template.id,
                day_number=day_definition.day_number,
                title=day_definition.title,
            )
            session.add(day)
            session.flush()
            for exercise_order, item in enumerate(
                day_definition.exercises,
                start=1,
            ):
                exercise = exercises_by_code.get(item.exercise_code)
                if exercise is None:
                    raise WorkoutCatalogError(
                        f"Unknown controlled exercise: {item.exercise_code}"
                    )
                session.add(
                    WorkoutTemplateExercise(
                        template_day_id=day.id,
                        exercise_id=exercise.id,
                        exercise_order=exercise_order,
                        sets=item.sets,
                        reps_min=item.reps_min,
                        reps_max=item.reps_max,
                        rest_seconds=item.rest_seconds,
                    )
                )

    adaptive_template = session.scalar(
        select(WorkoutTemplate).where(WorkoutTemplate.code == ADAPTIVE_TEMPLATE_CODE)
    )
    if adaptive_template is None:
        adaptive_template = WorkoutTemplate(
            code=ADAPTIVE_TEMPLATE_CODE,
            name="Адаптивная программа Stage 7",
            goal="muscle_gain",
            experience_level="beginner",
            workouts_per_week=1,
            duration_bucket="short",
            equipment="adaptive",
        )
        session.add(adaptive_template)
        session.flush()

    return CatalogStats(
        exercises=session.scalar(select(func.count(Exercise.id))) or 0,
        templates=session.scalar(select(func.count(WorkoutTemplate.id))) or 0,
        template_days=(
            session.scalar(select(func.count(WorkoutTemplateDay.id))) or 0
        ),
        template_exercises=(
            session.scalar(select(func.count(WorkoutTemplateExercise.id))) or 0
        ),
    )


def ensure_workout_catalog(
    session_factory: Callable[[], Session] = dbSession,
) -> CatalogStats:
    """Create the versioned controlled catalog idempotently."""
    with session_factory() as session:
        with session.begin():
            return _ensure_catalog_in_session(session)


def _load_plan_view(session: Session, plan: UserWorkoutPlan) -> WorkoutPlanView:
    template_name = session.scalar(
        select(WorkoutTemplate.name).where(WorkoutTemplate.id == plan.template_id)
    )
    if template_name is None:
        raise WorkoutCatalogError("Assigned template does not exist.")

    days: list[PlanDayView] = []
    plan_days = session.scalars(
        select(UserWorkoutPlanDay)
        .where(UserWorkoutPlanDay.plan_id == plan.id)
        .order_by(UserWorkoutPlanDay.day_number)
    ).all()
    for day in plan_days:
        items = session.execute(
            select(UserWorkoutPlanExercise, Exercise)
            .join(Exercise, Exercise.id == UserWorkoutPlanExercise.exercise_id)
            .where(UserWorkoutPlanExercise.plan_day_id == day.id)
            .order_by(UserWorkoutPlanExercise.exercise_order)
        ).all()
        plan_blocks = session.scalars(
            select(UserWorkoutPlanBlock)
            .where(UserWorkoutPlanBlock.plan_day_id == day.id)
            .order_by(UserWorkoutPlanBlock.block_order)
        ).all()
        exercise_views = {
            item.id: PlanExerciseView(
                order=item.exercise_order,
                name=item.exercise_name,
                primary_muscle_group=(
                    item.primary_muscle_group
                    if item.primary_muscle_group != "other"
                    else exercise.primary_muscle_group
                ),
                sets=item.sets,
                reps_min=item.reps_min,
                reps_max=item.reps_max,
                rest_seconds=item.rest_seconds,
                hint=item.hint,
                progression_strategy=item.progression_strategy,
                workout_format=(
                    next(
                        (block.workout_format for block in plan_blocks if block.id == item.plan_block_id),
                        "standard_sets",
                    )
                ),
                format_reps=item.format_reps,
                station_order=item.station_order,
            )
            for item, exercise in items
        }
        days.append(
            PlanDayView(
                day_number=day.day_number,
                title=day.title,
                exercises=tuple(exercise_views[item.id] for item, _ in items),
                blocks=tuple(
                    PlanBlockView(
                        order=block.block_order,
                        title=block.title,
                        workout_format=block.workout_format,
                        duration_seconds=block.duration_seconds,
                        target_rounds=block.target_rounds,
                        exercises=tuple(
                            exercise_views[item.id]
                            for item, _ in items if item.plan_block_id == block.id
                        ),
                    )
                    for block in plan_blocks
                ),
            )
        )
    return WorkoutPlanView(
        id=plan.id,
        template_name=template_name,
        days=tuple(days),
    )


def get_assigned_workout_plan(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> WorkoutPlanView | None:
    with session_factory() as session:
        plan = session.scalar(
            select(UserWorkoutPlan).where(UserWorkoutPlan.user_id == user_id)
        )
        return None if plan is None else _load_plan_view(session, plan)


def activate_generated_plan_for_profile(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
    *,
    create_if_missing: bool = True,
) -> PlanAssignmentResult | None:
    """Make the generated plan match the profile while preserving user programs."""
    with session_factory() as session:
        existing = session.scalar(
            select(UserWorkoutPlan).where(UserWorkoutPlan.user_id == user_id)
        )
        if existing is not None and existing.plan_source == "user_defined":
            return PlanAssignmentResult(
                plan=_load_plan_view(session, existing),
                created=False,
                fallback_notes=(),
            )
        if existing is None and not create_if_missing:
            return None
    return assign_workout_plan(user_id, session_factory)


def assign_workout_plan(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> PlanAssignmentResult:
    """Assign one deterministic active plan, replacing it only after profile changes."""
    with session_factory() as session:
        with session.begin():
            profile = session.get(FitnessProfile, user_id)
            if profile is None or profile.completed_at is None:
                raise FitnessProfileRequiredError(
                    "Сначала завершите фитнес-анкету."
                )

            normalized = normalize_profile(profile)
            signature = _profile_signature(normalized)
            _ensure_catalog_in_session(session)

            template_code = ADAPTIVE_TEMPLATE_CODE
            template = session.scalar(
                select(WorkoutTemplate).where(
                    WorkoutTemplate.code == template_code
                )
            )
            if template is None:
                raise WorkoutCatalogError(
                    f"Controlled template is missing: {template_code}"
                )

            existing = session.scalar(
                select(UserWorkoutPlan).where(
                    UserWorkoutPlan.user_id == user_id
                )
            )
            if (
                existing is not None
                and existing.template_id == template.id
                and existing.profile_signature == signature
            ):
                return PlanAssignmentResult(
                    plan=_load_plan_view(session, existing),
                    created=False,
                    fallback_notes=normalized.fallback_notes,
                )

            generated = generate_program(normalized)
            codes = {
                item.exercise_code
                for day in generated.days
                for item in day.exercises
            }
            codes.update(
                item.exercise_code
                for day in generated.days
                for block in day.blocks
                for item in block.exercises
            )
            exercises_by_code = {
                exercise.code: exercise
                for exercise in session.scalars(
                    select(Exercise).where(Exercise.code.in_(codes))
                ).all()
            }
            if len(exercises_by_code) != len(codes):
                raise WorkoutCatalogError("Generated plan references unknown exercise.")

            if existing is not None:
                session.delete(existing)
                session.flush()

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            plan = UserWorkoutPlan(
                user_id=user_id,
                template_id=template.id,
                profile_signature=signature,
                assigned_at=now,
                updated_at=now,
            )
            session.add(plan)
            session.flush()
            for generated_day in generated.days:
                plan_day = UserWorkoutPlanDay(
                    plan_id=plan.id,
                    day_number=generated_day.day_number,
                    title=generated_day.title,
                )
                session.add(plan_day)
                session.flush()
                for exercise_order, item in enumerate(
                    generated_day.exercises,
                    start=1,
                ):
                    exercise = exercises_by_code[item.exercise_code]
                    session.add(
                        UserWorkoutPlanExercise(
                            plan_day_id=plan_day.id,
                            exercise_id=exercise.id,
                            exercise_order=exercise_order,
                            exercise_name=exercise.name,
                            primary_muscle_group=exercise.primary_muscle_group,
                            sets=item.sets,
                            reps_min=item.reps_min,
                            reps_max=item.reps_max,
                            rest_seconds=item.rest_seconds,
                            hint=exercise.hint,
                            progression_strategy=item.progression_strategy,
                        )
                    )
                next_order = len(generated_day.exercises) + 1
                for block_order, generated_block in enumerate(generated_day.blocks, start=1):
                    plan_block = UserWorkoutPlanBlock(
                        plan_day_id=plan_day.id,
                        block_order=block_order,
                        title=generated_block.title,
                        workout_format=generated_block.workout_format,
                        duration_seconds=generated_block.duration_seconds,
                        target_rounds=generated_block.target_rounds,
                    )
                    session.add(plan_block)
                    session.flush()
                    for station_order, item in enumerate(generated_block.exercises, start=1):
                        exercise = exercises_by_code[item.exercise_code]
                        session.add(UserWorkoutPlanExercise(
                            plan_day_id=plan_day.id,
                            plan_block_id=plan_block.id,
                            exercise_id=exercise.id,
                            exercise_order=next_order,
                            exercise_name=exercise.name,
                            primary_muscle_group=exercise.primary_muscle_group,
                            sets=1,
                            reps_min=item.reps,
                            reps_max=item.reps,
                            rest_seconds=0,
                            hint=exercise.hint,
                            progression_strategy=None,
                            format_reps=item.reps,
                            station_order=station_order,
                        ))
                        next_order += 1
            session.flush()
            return PlanAssignmentResult(
                plan=_load_plan_view(session, plan),
                created=True,
                fallback_notes=normalized.fallback_notes,
            )


def day_primary_muscle_groups(day: PlanDayView) -> tuple[str, ...]:
    """Return ordered unique primary muscle groups from the day's exercises."""
    return tuple(dict.fromkeys(item.primary_muscle_group for item in day.exercises))


def format_workout_plan(
    plan: WorkoutPlanView,
    fallback_notes: tuple[str, ...] = (),
) -> str:
    """Render every required plan field as a compact Russian message."""
    lines = [f"Ваш недельный план: {escape(plan.template_name)}"]
    for note in fallback_notes:
        lines.append(f"Важно: {escape(note)}")
    for day in plan.days:
        lines.append("")
        groups = ", ".join(day_primary_muscle_groups(day))
        lines.append(f"{day.day_number}. {escape(day.title)} — {escape(groups)}")
        for exercise in day.exercises:
            if exercise.workout_format != "standard_sets":
                continue
            lines.append(
                f"{exercise.order}) {escape(exercise.name)} — "
                f"{exercise.sets}×{exercise.reps_min}–{exercise.reps_max}, "
                f"отдых {exercise.rest_seconds} сек."
            )
            lines.append(f"Группа мышц: {escape(exercise.primary_muscle_group)}")
            lines.append(f"Подсказка: {escape(exercise.hint)}")
        for block in day.blocks:
            details = []
            if block.duration_seconds:
                details.append(f"{block.duration_seconds // 60} мин")
            if block.target_rounds:
                details.append(f"{block.target_rounds} круга")
            suffix = f" — {', '.join(details)}" if details else ""
            lines.append(f"{escape(block.title)}{suffix}")
            for exercise in block.exercises:
                lines.append(
                    f"• {escape(exercise.name)} — {exercise.format_reps} повторений"
                )
    return "\n".join(lines)


def format_workout_plan_preview(
    plan: WorkoutPlanView,
    profile: FitnessProfile,
    fallback_notes: tuple[str, ...] = (),
) -> str:
    """Render a safe plan preview before the trial has started."""
    lines = ["Ваш персональный план подготовлен."]
    lines.append(f"Цель: {escape(GOAL_NAMES.get(profile.goal, DEFAULT_GOAL))}")
    lines.append(f"Тренировок в неделю: {profile.workouts_per_week}")
    lines.append(f"Примерная длительность: {profile.session_duration_minutes} мин")
    for note in fallback_notes:
        lines.append(f"Важно: {escape(note)}")
    for day in plan.days:
        groups = day_primary_muscle_groups(day)
        lines.append(f"{day.day_number}. {escape(day.title)} — Мышцы: {escape(', '.join(groups))}")
    lines.append("")
    lines.append("Пробный период начнётся только при запуске первой тренировки.")
    return "\n".join(lines)
