"""Deterministic parsing and atomic assignment of user-provided programs."""

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Callable, Protocol
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Exercise,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanExercise,
    WorkoutTemplate,
    dbSession,
)
from services.exercise_catalog import EXERCISE_DEFINITIONS, ExerciseDefinition
from services.workout_plans import (
    WorkoutCatalogError,
    WorkoutPlanView,
    _ensure_catalog_in_session,
    _load_plan_view,
)
from services.workout_progression import ProgressionStrategy


MAX_INPUT_LENGTH = 6000
MAX_DAYS = 7
MAX_EXERCISES_PER_DAY = 15
MAX_SETS = 20
MAX_REPS = 100
USER_TEMPLATE_CODE = "v4_user_defined_standard"


class AdaptationMode(StrEnum):
    STRICT = "strict"
    REPLACEMENTS = "replacements"
    ADAPTIVE = "adaptive"


MODE_LABELS = {
    AdaptationMode.STRICT: "Следовать строго",
    AdaptationMode.REPLACEMENTS: "Разрешать замены",
    AdaptationMode.ADAPTIVE: "Адаптировать по прогрессу",
}


class ParseIssueCode(StrEnum):
    INPUT_TOO_LONG = "input_too_long"
    UNSUPPORTED_FORMAT = "unsupported_format"
    MALFORMED_PRESCRIPTION = "malformed_prescription"
    UNRESOLVED_EXERCISE = "unresolved_exercise"
    AMBIGUOUS_EXERCISE = "ambiguous_exercise"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    DUPLICATE_EXERCISE = "duplicate_exercise"
    EMPTY_DAY = "empty_day"
    LIMIT_EXCEEDED = "limit_exceeded"
    NO_DAYS = "no_days"


@dataclass(frozen=True)
class ParseIssue:
    code: ParseIssueCode
    line_number: int | None
    text: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserProgramExerciseDraft:
    exercise_code: str
    exercise_name: str
    sets: int
    reps_min: int
    reps_max: int


@dataclass(frozen=True)
class UserProgramDayDraft:
    day_number: int
    title: str
    exercises: tuple[UserProgramExerciseDraft, ...]


@dataclass(frozen=True)
class UserProgramDraft:
    mode: AdaptationMode
    days: tuple[UserProgramDayDraft, ...]

    def to_payload(self) -> dict:
        return {
            "mode": self.mode.value,
            "days": [
                {
                    "day_number": day.day_number,
                    "title": day.title,
                    "exercises": [exercise.__dict__ for exercise in day.exercises],
                }
                for day in self.days
            ],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "UserProgramDraft":
        return cls(
            mode=AdaptationMode(payload["mode"]),
            days=tuple(
                UserProgramDayDraft(
                    day_number=int(day["day_number"]),
                    title=str(day["title"]),
                    exercises=tuple(
                        UserProgramExerciseDraft(**exercise)
                        for exercise in day["exercises"]
                    ),
                )
                for day in payload["days"]
            ),
        )


@dataclass(frozen=True)
class UserProgramParseResult:
    draft: UserProgramDraft | None
    issues: tuple[ParseIssue, ...]

    @property
    def valid(self) -> bool:
        return self.draft is not None and not self.issues


@dataclass(frozen=True)
class UserProgramAssignmentResult:
    plan: WorkoutPlanView
    created: bool


class UserProgramParser(Protocol):
    def parse(
        self,
        text: str,
        mode: AdaptationMode,
        *,
        training_environment: str | None = None,
    ) -> UserProgramParseResult: ...


_DAY_NAMES = {
    "пн": "Понедельник", "понедельник": "Понедельник",
    "вт": "Вторник", "вторник": "Вторник",
    "ср": "Среда", "среда": "Среда",
    "чт": "Четверг", "четверг": "Четверг",
    "пт": "Пятница", "пятница": "Пятница",
    "сб": "Суббота", "суббота": "Суббота",
    "вс": "Воскресенье", "воскресенье": "Воскресенье",
}
_EXPLICIT_ALIASES = {
    "жим штанги лежа": "barbell_bench_press",
    "жим лежа": "barbell_bench_press",
    "жим гантелей под углом": "incline_dumbbell_press",
    "наклонные гантели": "incline_dumbbell_press",
    "разведение в тренажере": "pec_deck_fly",
    "трицепс на блоке": "triceps_pushdown",
    "тяга верхнего блока": "lat_pulldown",
    "тяга нижнего блока": "seated_row",
}
_FORMAT_MARKERS = re.compile(r"\b(amrap|emom|for\s*time|на\s+время|круг(?:а|ов)?)\b", re.I)
_PRESCRIPTION = re.compile(
    r"^(?P<name>.+?)\s*(?:[-—:]\s*)?(?P<sets>\d{1,2})\s*"
    r"(?:[xх×]|подход(?:а|ов)?\s+по|по)\s*"
    r"(?P<min>\d{1,3})(?:\s*[-–—]\s*(?P<max>\d{1,3}))?\s*$",
    re.I,
)
_DAY_NUMBER = re.compile(r"^день\s+(\d{1,2})(?:\s*[-—:]\s*(.*))?$", re.I)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("ё", "е"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())


def _aliases() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for definition in EXERCISE_DEFINITIONS:
        for value in (definition.name, definition.alternative_name):
            if value:
                result.setdefault(_normalize(value), set()).add(definition.code)
    for alias, code in _EXPLICIT_ALIASES.items():
        result.setdefault(_normalize(alias), set()).add(code)
    return result


class DeterministicRussianProgramParser:
    """Bounded baseline parser; uncertain resolution is always rejected."""

    def __init__(self) -> None:
        self._definitions = {item.code: item for item in EXERCISE_DEFINITIONS}
        self._aliases = _aliases()

    def parse(self, text, mode, *, training_environment=None):
        if not isinstance(text, str) or len(text) > MAX_INPUT_LENGTH:
            return UserProgramParseResult(None, (
                ParseIssue(ParseIssueCode.INPUT_TOO_LONG, None, "Текст программы слишком длинный."),
            ))
        if _FORMAT_MARKERS.search(text):
            return UserProgramParseResult(None, (
                ParseIssue(
                    ParseIssueCode.UNSUPPORTED_FORMAT, None,
                    "Свободный импорт AMRAP/EMOM/кругов пока не поддерживается.",
                ),
            ))

        days: list[dict] = []
        issues: list[ParseIssue] = []
        current: dict | None = None
        for line_number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            normalized = _normalize(line.rstrip(":"))
            day_match = _DAY_NUMBER.match(line.rstrip(":"))
            weekday = _DAY_NAMES.get(normalized)
            if day_match or weekday:
                if current is not None and not current["exercises"]:
                    issues.append(ParseIssue(
                        ParseIssueCode.EMPTY_DAY, line_number - 1,
                        f"{current['title']}: нет упражнений.",
                    ))
                if len(days) >= MAX_DAYS:
                    issues.append(ParseIssue(
                        ParseIssueCode.LIMIT_EXCEEDED, line_number,
                        f"Можно сохранить не более {MAX_DAYS} дней.",
                    ))
                    continue
                title = weekday or (
                    f"День {day_match.group(1)}"
                    + (f" — {day_match.group(2).strip()}" if day_match.group(2) else "")
                )
                current = {"title": title, "exercises": [], "codes": set()}
                days.append(current)
                continue

            if current is None:
                current = {"title": "День 1", "exercises": [], "codes": set()}
                days.append(current)
            exercise_line = re.sub(r"^\s*\d+[.)]\s*", "", line)
            match = _PRESCRIPTION.match(exercise_line)
            if not match:
                issues.append(ParseIssue(
                    ParseIssueCode.MALFORMED_PRESCRIPTION, line_number,
                    "Укажите упражнение, подходы и повторы, например 3×10.",
                ))
                continue
            sets = int(match.group("sets"))
            reps_min = int(match.group("min"))
            reps_max = int(match.group("max") or reps_min)
            if not (1 <= sets <= MAX_SETS and 1 <= reps_min <= reps_max <= MAX_REPS):
                issues.append(ParseIssue(
                    ParseIssueCode.MALFORMED_PRESCRIPTION, line_number,
                    "Количество подходов или повторений вне допустимого диапазона.",
                ))
                continue
            resolution = self._resolve(match.group("name"))
            if isinstance(resolution, ParseIssue):
                issues.append(ParseIssue(
                    resolution.code, line_number, resolution.text, resolution.candidates
                ))
                continue
            definition = resolution
            if training_environment and training_environment not in definition.environments:
                issues.append(ParseIssue(
                    ParseIssueCode.ENVIRONMENT_MISMATCH, line_number,
                    f"«{definition.name}» не подходит для выбранного места тренировок.",
                ))
                continue
            if definition.code in current["codes"]:
                issues.append(ParseIssue(
                    ParseIssueCode.DUPLICATE_EXERCISE, line_number,
                    f"«{definition.name}» уже есть в этом дне.",
                ))
                continue
            if len(current["exercises"]) >= MAX_EXERCISES_PER_DAY:
                issues.append(ParseIssue(
                    ParseIssueCode.LIMIT_EXCEEDED, line_number,
                    f"В одном дне можно сохранить не более {MAX_EXERCISES_PER_DAY} упражнений.",
                ))
                continue
            current["codes"].add(definition.code)
            current["exercises"].append(UserProgramExerciseDraft(
                definition.code, definition.name, sets, reps_min, reps_max
            ))

        if current is not None and not current["exercises"]:
            issues.append(ParseIssue(ParseIssueCode.EMPTY_DAY, None, f"{current['title']}: нет упражнений."))
        if not days:
            issues.append(ParseIssue(ParseIssueCode.NO_DAYS, None, "Не найдено ни одного тренировочного дня."))
        if issues:
            return UserProgramParseResult(None, tuple(issues))
        return UserProgramParseResult(UserProgramDraft(
            mode=AdaptationMode(mode),
            days=tuple(
                UserProgramDayDraft(index, day["title"], tuple(day["exercises"]))
                for index, day in enumerate(days, start=1)
            ),
        ), ())

    def _resolve(self, name: str) -> ExerciseDefinition | ParseIssue:
        normalized = _normalize(name)
        exact = self._aliases.get(normalized, set())
        if len(exact) == 1:
            return self._definitions[next(iter(exact))]
        if len(exact) > 1:
            names = tuple(sorted(self._definitions[code].name for code in exact))
            return ParseIssue(ParseIssueCode.AMBIGUOUS_EXERCISE, None, "Название неоднозначно.", names)
        scored = sorted(
            (
                (SequenceMatcher(None, normalized, alias).ratio(), code)
                for alias, codes in self._aliases.items() for code in codes
            ),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.9:
            same = {code for score, code in scored if score >= scored[0][0] - 0.03}
            if len(same) == 1:
                return self._definitions[next(iter(same))]
            return ParseIssue(
                ParseIssueCode.AMBIGUOUS_EXERCISE, None, "Название неоднозначно.",
                tuple(sorted(self._definitions[code].name for code in same)),
            )
        tokens = set(normalized.split())
        candidates = {
            code
            for alias, codes in self._aliases.items()
            if tokens and tokens.issubset(set(alias.split()))
            for code in codes
        }
        if len(candidates) > 1:
            return ParseIssue(
                ParseIssueCode.AMBIGUOUS_EXERCISE, None, "Название подходит к нескольким упражнениям.",
                tuple(sorted(self._definitions[code].name for code in candidates)[:5]),
            )
        return ParseIssue(ParseIssueCode.UNRESOLVED_EXERCISE, None, f"Не удалось распознать упражнение «{name.strip()}».")


def format_user_program_preview(draft: UserProgramDraft) -> str:
    lines = ["Я понял программу так:"]
    for day in draft.days:
        lines.extend(("", day.title))
        for exercise in day.exercises:
            reps = str(exercise.reps_min) if exercise.reps_min == exercise.reps_max else f"{exercise.reps_min}–{exercise.reps_max}"
            lines.append(f"• {exercise.exercise_name} — {exercise.sets}×{reps}")
    lines.extend(("", f"Режим: {MODE_LABELS[draft.mode]}"))
    return "\n".join(lines)


def format_parse_issues(issues: tuple[ParseIssue, ...]) -> str:
    lines = ["Не удалось однозначно разобрать программу:"]
    for issue in issues[:8]:
        prefix = f"Строка {issue.line_number}: " if issue.line_number else ""
        lines.append(f"• {prefix}{issue.text}")
        if issue.candidates:
            lines.append("  Варианты: " + "; ".join(issue.candidates))
    lines.append("\nИсправьте текст и отправьте программу заново.")
    return "\n".join(lines)


def _signature(draft: UserProgramDraft) -> str:
    encoded = json.dumps(draft.to_payload(), ensure_ascii=True, sort_keys=True)
    return sha256(encoded.encode()).hexdigest()


def _strategy(definition: ExerciseDefinition, mode: AdaptationMode) -> str | None:
    if mode != AdaptationMode.ADAPTIVE:
        return None
    if definition.progression_type == "bodyweight_reps":
        return ProgressionStrategy.BODYWEIGHT_REPS
    if definition.progression_type == "external_load_reps":
        return ProgressionStrategy.HYPERTROPHY_LOAD_REPS
    return None


def assign_user_program(
    user_id: int,
    draft: UserProgramDraft,
    session_factory: Callable[[], Session] = dbSession,
) -> UserProgramAssignmentResult:
    """Atomically assign a validated structured draft; repeated confirm is safe."""
    signature = _signature(draft)
    with session_factory() as session:
        with session.begin():
            _ensure_catalog_in_session(session)
            template = session.scalar(select(WorkoutTemplate).where(WorkoutTemplate.code == USER_TEMPLATE_CODE))
            if template is None:
                template = WorkoutTemplate(
                    code=USER_TEMPLATE_CODE, name="Моя программа", goal="muscle_gain",
                    experience_level="beginner", workouts_per_week=1,
                    duration_bucket="standard", equipment="adaptive",
                )
                session.add(template)
                session.flush()
            existing = session.scalar(select(UserWorkoutPlan).where(UserWorkoutPlan.user_id == user_id))
            if (
                existing is not None and existing.plan_source == "user_defined"
                and existing.profile_signature == signature
                and existing.adaptation_mode == draft.mode.value
            ):
                return UserProgramAssignmentResult(_load_plan_view(session, existing), False)
            codes = {exercise.exercise_code for day in draft.days for exercise in day.exercises}
            rows = session.scalars(select(Exercise).where(Exercise.code.in_(codes))).all()
            by_code = {row.code: row for row in rows}
            if set(by_code) != codes:
                raise WorkoutCatalogError("Draft references an unknown controlled exercise.")
            if existing is not None:
                session.delete(existing)
                session.flush()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            plan = UserWorkoutPlan(
                user_id=user_id, template_id=template.id, profile_signature=signature,
                assigned_at=now, updated_at=now, plan_source="user_defined",
                adaptation_mode=draft.mode.value,
            )
            session.add(plan)
            session.flush()
            definitions = {item.code: item for item in EXERCISE_DEFINITIONS}
            for day in draft.days:
                stored_day = UserWorkoutPlanDay(
                    plan_id=plan.id, day_number=day.day_number, title=day.title
                )
                session.add(stored_day)
                session.flush()
                for order, exercise in enumerate(day.exercises, start=1):
                    row = by_code[exercise.exercise_code]
                    definition = definitions[exercise.exercise_code]
                    session.add(UserWorkoutPlanExercise(
                        plan_day_id=stored_day.id, exercise_id=row.id,
                        exercise_order=order, exercise_name=row.name,
                        primary_muscle_group=row.primary_muscle_group,
                        sets=exercise.sets, reps_min=exercise.reps_min,
                        reps_max=exercise.reps_max, rest_seconds=90,
                        hint=row.hint, progression_strategy=_strategy(definition, draft.mode),
                    ))
            session.flush()
            return UserProgramAssignmentResult(_load_plan_view(session, plan), True)
