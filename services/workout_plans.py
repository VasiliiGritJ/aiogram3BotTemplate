"""Controlled exercise catalog and deterministic weekly workout plans."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import (
    Exercise,
    FitnessProfile,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
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
    validate_exercise_definition,
)


CATALOG_VERSION = 2
DEFAULT_GOAL = "muscle_gain"
DEFAULT_EXPERIENCE = "beginner"
DEFAULT_EQUIPMENT = "gym"
MAX_TEMPLATE_WORKOUTS_PER_WEEK = 4
SHORT_SESSION_MAX_MINUTES = 45

SUPPORTED_GOALS = {"muscle_gain", "fat_loss"}
SUPPORTED_EXPERIENCE = {"beginner", "some_experience"}

GOAL_NAMES = {
    "muscle_gain": "Базовая силовая программа",
    "fat_loss": "Общая физическая подготовка",
}
EXPERIENCE_NAMES = {
    "beginner": "новичок",
    "some_experience": "с опытом",
    "experienced": "опытный",
}

class WorkoutPlanError(RuntimeError):
    """Base error for deterministic workout planning."""


class FitnessProfileRequiredError(WorkoutPlanError):
    """Raised when a workout plan is requested before onboarding."""


class WorkoutPlanNotReadyError(WorkoutPlanError):
    """Raised when Stage 7 profile choices need the Stage 7C generator."""


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
    workouts_per_week: int
    duration_bucket: str
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


@dataclass(frozen=True)
class PlanDayView:
    day_number: int
    title: str
    exercises: tuple[PlanExerciseView, ...]


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
    for goal in sorted(SUPPORTED_GOALS):
        for experience_level in sorted(SUPPORTED_EXPERIENCE):
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
    """Map persisted onboarding values to the finite template matrix."""
    fallback_notes: list[str] = []

    goal = profile.goal
    if goal == "strength":
        raise WorkoutPlanNotReadyError(
            "Программа для цели «Стать сильнее» появится на следующем этапе. "
            "Ваш профиль сохранён."
        )
    if goal not in SUPPORTED_GOALS:
        goal = DEFAULT_GOAL
        fallback_notes.append(
            "Цель не распознана — использован базовый силовой шаблон."
        )

    experience = profile.experience_level
    if experience in {"some_experience", "intermediate"}:
        experience = "some_experience"
    elif experience in {"experienced", "advanced"}:
        experience = "some_experience"
        fallback_notes.append(
            "Для опытного уровня пока использован ближайший доступный шаблон с опытом."
        )
    elif experience not in SUPPORTED_EXPERIENCE:
        experience = DEFAULT_EXPERIENCE
        fallback_notes.append(
            "Уровень опыта не распознан — использован уровень новичка."
        )

    try:
        requested_workouts = int(profile.workouts_per_week)
    except (TypeError, ValueError):
        requested_workouts = 1
    training_environment = getattr(profile, "training_environment", None)
    if training_environment not in {None, "gym"}:
        raise WorkoutPlanNotReadyError(
            "Программа для выбранного места тренировок появится на следующем "
            "этапе. Ваш профиль сохранён."
        )
    if training_environment is not None and requested_workouts > 4:
        raise WorkoutPlanNotReadyError(
            "Программа на 5–6 тренировок в неделю появится на следующем этапе. "
            "Ваш профиль сохранён."
        )
    workouts_per_week = min(
        max(requested_workouts, 1),
        MAX_TEMPLATE_WORKOUTS_PER_WEEK,
    )
    if workouts_per_week != requested_workouts:
        fallback_notes.append(
            f"Для первой версии назначено {workouts_per_week} тренировок "
            "в неделю."
        )

    try:
        duration = int(profile.session_duration_minutes)
    except (TypeError, ValueError):
        duration = SHORT_SESSION_MAX_MINUTES
        fallback_notes.append(
            "Длительность не распознана — выбран короткий формат."
        )
    duration_bucket = (
        "short" if duration <= SHORT_SESSION_MAX_MINUTES else "standard"
    )
    has_limitations = _has_limitations(profile.limitations)
    if has_limitations:
        fallback_notes.append(LIMITATIONS_NOTICE)

    return NormalizedProfile(
        goal=goal,
        experience_level=experience,
        workouts_per_week=workouts_per_week,
        duration_bucket=duration_bucket,
        equipment=DEFAULT_EQUIPMENT,
        has_limitations=has_limitations,
        fallback_notes=tuple(fallback_notes),
    )


def _profile_signature(profile: NormalizedProfile) -> str:
    payload = {
        "catalog_version": CATALOG_VERSION,
        "duration_bucket": profile.duration_bucket,
        "equipment": profile.equipment,
        "experience_level": profile.experience_level,
        "goal": profile.goal,
        "has_limitations": profile.has_limitations,
        "workouts_per_week": profile.workouts_per_week,
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


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
        days.append(
            PlanDayView(
                day_number=day.day_number,
                title=day.title,
                exercises=tuple(
                    PlanExerciseView(
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
                    )
                    for item, exercise in items
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

            template_code = _template_code(
                normalized.goal,
                normalized.experience_level,
                normalized.workouts_per_week,
                normalized.duration_bucket,
            )
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

            selected_days: list[
                tuple[WorkoutTemplateDay, list[tuple[WorkoutTemplateExercise, Exercise]]]
            ] = []
            template_days = session.scalars(
                select(WorkoutTemplateDay)
                .where(WorkoutTemplateDay.template_id == template.id)
                .order_by(WorkoutTemplateDay.day_number)
            ).all()
            for template_day in template_days:
                rows = session.execute(
                    select(WorkoutTemplateExercise, Exercise)
                    .join(
                        Exercise,
                        Exercise.id == WorkoutTemplateExercise.exercise_id,
                    )
                    .where(
                        WorkoutTemplateExercise.template_day_id == template_day.id
                    )
                    .order_by(WorkoutTemplateExercise.exercise_order)
                ).all()
                selected_days.append((template_day, list(rows)))

            if not selected_days:
                raise WorkoutCatalogError("Controlled template has no days.")

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
            for template_day, selected_exercises in selected_days:
                plan_day = UserWorkoutPlanDay(
                    plan_id=plan.id,
                    day_number=template_day.day_number,
                    title=template_day.title,
                )
                session.add(plan_day)
                session.flush()
                for exercise_order, (item, exercise) in enumerate(
                    selected_exercises,
                    start=1,
                ):
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
                        )
                    )
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
            lines.append(
                f"{exercise.order}) {escape(exercise.name)} — "
                f"{exercise.sets}×{exercise.reps_min}–{exercise.reps_max}, "
                f"отдых {exercise.rest_seconds} сек."
            )
            lines.append(f"Группа мышц: {escape(exercise.primary_muscle_group)}")
            lines.append(f"Подсказка: {escape(exercise.hint)}")
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
