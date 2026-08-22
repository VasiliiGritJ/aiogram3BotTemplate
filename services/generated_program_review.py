"""Deterministic offline review export for the controlled Stage 7 generator.

Run with ``python -m services.generated_program_review``.  The module uses no
database, Telegram, or network resources; its files are review artefacts only.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from services.exercise_catalog import exercise_definition_by_code
from services.workout_plans import NormalizedProfile, generate_program


REVIEW_ENVIRONMENTS = ("home", "street", "functional_gym")
REVIEW_GOALS = ("muscle_gain", "strength", "fat_loss")
REVIEW_EXPERIENCE = ("beginner", "intermediate", "advanced")
REVIEW_FREQUENCIES = (2, 3, 4, 5, 6)
REVIEW_DURATIONS = (30, 45, 60, 90)

CSV_COLUMNS = (
    "environment",
    "goal",
    "experience",
    "frequency",
    "duration",
    "program_key",
    "day_number",
    "day_name",
    "workout_format",
    "exercise_order",
    "stable_exercise_id",
    "display_name",
    "primary_muscle",
    "equipment",
    "movement_pattern",
    "sets",
    "reps_min",
    "reps_max",
    "timed_prescription",
    "progression_strategy",
)


@dataclass(frozen=True)
class GeneratedProgramReview:
    rows: tuple[dict[str, str], ...]
    combinations_count: int
    program_days: int
    validation_failures: tuple[str, ...]
    duplicate_warnings: tuple[str, ...]
    core_frequency_warnings: tuple[str, ...]
    direct_arm_coverage_warnings: tuple[str, ...]
    top_exercises_by_environment: dict[str, tuple[tuple[str, int], ...]]


def _profile_key(profile: NormalizedProfile) -> str:
    return (
        f"{profile.training_environment}:{profile.goal}:{profile.experience_level}:"
        f"{profile.workouts_per_week}d:{profile.session_duration_minutes}m"
    )


def _profile_combinations() -> Iterable[NormalizedProfile]:
    for environment in REVIEW_ENVIRONMENTS:
        for goal in REVIEW_GOALS:
            for experience in REVIEW_EXPERIENCE:
                for frequency in REVIEW_FREQUENCIES:
                    for duration in REVIEW_DURATIONS:
                        yield NormalizedProfile(
                            goal=goal,
                            experience_level=experience,
                            training_environment=environment,
                            workouts_per_week=frequency,
                            session_duration_minutes=duration,
                            equipment=environment,
                            has_limitations=False,
                            fallback_notes=(),
                        )


def _text(value: object | None) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _timed_prescription(workout_format: str, duration_seconds: int | None, target_rounds: int | None) -> str:
    if duration_seconds:
        return f"{workout_format}: {duration_seconds // 60} min"
    if target_rounds:
        return f"{workout_format}: {target_rounds} rounds"
    return workout_format


def _row(
    profile: NormalizedProfile,
    *,
    program_key: str,
    day_number: int,
    day_name: str,
    workout_format: str,
    exercise_order: int,
    exercise_code: str,
    sets: int | None,
    reps_min: int | None,
    reps_max: int | None,
    timed_prescription: str = "",
    progression_strategy: object | None = None,
) -> dict[str, str]:
    definition = exercise_definition_by_code(exercise_code)
    if definition is None:
        raise ValueError(f"Unknown catalog exercise: {exercise_code}")
    return {
        "environment": profile.training_environment,
        "goal": profile.goal,
        "experience": profile.experience_level,
        "frequency": str(profile.workouts_per_week),
        "duration": str(profile.session_duration_minutes),
        "program_key": program_key,
        "day_number": str(day_number),
        "day_name": day_name,
        "workout_format": workout_format,
        "exercise_order": str(exercise_order),
        # The catalog code is the stable offline identifier; a database ID is
        # deliberately not used because this exporter never opens a database.
        "stable_exercise_id": definition.code,
        "display_name": definition.name,
        "primary_muscle": definition.primary_muscle_group,
        "equipment": definition.equipment,
        "movement_pattern": definition.movement_pattern,
        "sets": "" if sets is None else str(sets),
        "reps_min": "" if reps_min is None else str(reps_min),
        "reps_max": "" if reps_max is None else str(reps_max),
        "timed_prescription": timed_prescription,
        "progression_strategy": _text(progression_strategy),
    }


def _validate_day(
    profile: NormalizedProfile,
    program_key: str,
    day_number: int,
    codes: list[str],
    failures: list[str],
    duplicate_warnings: list[str],
) -> None:
    if not codes:
        failures.append(f"{program_key}/day-{day_number}: empty workout")
        return
    duplicates = sorted(code for code, count in Counter(codes).items() if count > 1)
    if duplicates:
        message = f"{program_key}/day-{day_number}: duplicate {', '.join(duplicates)}"
        failures.append(message)
        duplicate_warnings.append(message)
    for code in codes:
        definition = exercise_definition_by_code(code)
        if definition is None:
            failures.append(f"{program_key}/day-{day_number}: unknown {code}")
            continue
        if profile.training_environment not in definition.environments:
            failures.append(f"{program_key}/day-{day_number}: environment incompatible {code}")
        if profile.training_environment == "home" and definition.equipment != "bodyweight":
            failures.append(f"{program_key}/day-{day_number}: home requires gym equipment {code}")
        if profile.experience_level not in definition.experience_levels:
            failures.append(f"{program_key}/day-{day_number}: experience incompatible {code}")


def build_generated_program_review() -> GeneratedProgramReview:
    """Generate and validate all 540 specified profiles deterministically."""
    rows: list[dict[str, str]] = []
    failures: list[str] = []
    duplicate_warnings: list[str] = []
    core_warnings: list[str] = []
    arm_warnings: list[str] = []
    usage: dict[str, Counter[str]] = {environment: Counter() for environment in REVIEW_ENVIRONMENTS}
    combinations = 0
    days_count = 0

    for profile in _profile_combinations():
        combinations += 1
        program_key = _profile_key(profile)
        program = generate_program(profile)
        if program != generate_program(profile):
            failures.append(f"{program_key}: non-deterministic generation")

        weekly_definitions = []
        for day in program.days:
            days_count += 1
            codes: list[str] = []
            for order, exercise in enumerate(day.exercises, start=1):
                codes.append(exercise.exercise_code)
                definition = exercise_definition_by_code(exercise.exercise_code)
                if definition is not None:
                    weekly_definitions.append(definition)
                    usage[profile.training_environment][definition.code] += 1
                rows.append(_row(
                    profile,
                    program_key=program_key,
                    day_number=day.day_number,
                    day_name=day.title,
                    workout_format="standard_sets",
                    exercise_order=order,
                    exercise_code=exercise.exercise_code,
                    sets=exercise.sets,
                    reps_min=exercise.reps_min,
                    reps_max=exercise.reps_max,
                    progression_strategy=exercise.progression_strategy,
                ))
            order = len(day.exercises)
            for block in day.blocks:
                prescription = _timed_prescription(
                    block.workout_format,
                    block.duration_seconds,
                    block.target_rounds,
                )
                for block_exercise in block.exercises:
                    order += 1
                    codes.append(block_exercise.exercise_code)
                    definition = exercise_definition_by_code(block_exercise.exercise_code)
                    if definition is not None:
                        weekly_definitions.append(definition)
                        usage[profile.training_environment][definition.code] += 1
                    rows.append(_row(
                        profile,
                        program_key=program_key,
                        day_number=day.day_number,
                        day_name=day.title,
                        workout_format=block.workout_format,
                        exercise_order=order,
                        exercise_code=block_exercise.exercise_code,
                        sets=None,
                        reps_min=block_exercise.reps,
                        reps_max=block_exercise.reps,
                        timed_prescription=prescription,
                        progression_strategy="timed_conditioning",
                    ))
            _validate_day(profile, program_key, day.day_number, codes, failures, duplicate_warnings)

        core_count = sum(item.primary_muscle_group == "core" for item in weekly_definitions)
        if core_count > 2:
            core_warnings.append(f"{program_key}: core appears {core_count} times")
        if (
            profile.goal == "muscle_gain"
            and profile.workouts_per_week >= 3
            and profile.session_duration_minutes >= 60
        ):
            direct_muscles = {
                item.primary_muscle_group
                for item in weekly_definitions
                if item.movement_pattern == "isolation"
            }
            missing = {"biceps", "triceps"} - direct_muscles
            if missing:
                arm_warnings.append(
                    f"{program_key}: missing direct {', '.join(sorted(missing))}"
                )

    top_usage = {
        environment: tuple(counter.most_common(10))
        for environment, counter in usage.items()
    }
    return GeneratedProgramReview(
        rows=tuple(rows),
        combinations_count=combinations,
        program_days=days_count,
        validation_failures=tuple(failures),
        duplicate_warnings=tuple(duplicate_warnings),
        core_frequency_warnings=tuple(core_warnings),
        direct_arm_coverage_warnings=tuple(arm_warnings),
        top_exercises_by_environment=top_usage,
    )


def _warning_section(title: str, warnings: tuple[str, ...]) -> list[str]:
    lines = [f"## {title}", ""]
    if not warnings:
        return lines + ["Нет.", ""]
    lines.append(f"Всего: {len(warnings)}")
    lines.extend(f"- {message}" for message in warnings[:10])
    if len(warnings) > 10:
        lines.append("- … остальные примеры доступны по CSV.")
    lines.append("")
    return lines


def format_review_summary(review: GeneratedProgramReview) -> str:
    lines = [
        "# Stage 7 generated-program review",
        "",
        f"- Profile combinations: {review.combinations_count}",
        f"- Program days: {review.program_days}",
        f"- Exercise rows: {len(review.rows)}",
        f"- Validation failures: {len(review.validation_failures)}",
        "",
        "## Top exercise usage by environment",
        "",
    ]
    for environment in REVIEW_ENVIRONMENTS:
        lines.append(f"### {environment}")
        for code, count in review.top_exercises_by_environment[environment]:
            definition = exercise_definition_by_code(code)
            name = definition.name if definition is not None else code
            lines.append(f"- {code} — {name}: {count}")
        lines.append("")
    lines.extend(_warning_section("Duplicate warnings", review.duplicate_warnings))
    lines.extend(_warning_section("Environment compatibility failures", tuple(
        item for item in review.validation_failures if "environment incompatible" in item
        or "home requires gym" in item
    )))
    lines.extend(_warning_section("Beginner suitability failures", tuple(
        item for item in review.validation_failures if "experience incompatible" in item
    )))
    lines.extend(_warning_section("Direct biceps/triceps weekly coverage", review.direct_arm_coverage_warnings))
    lines.extend(_warning_section("Core frequency warnings", review.core_frequency_warnings))
    lines.extend([
        "## Deterministic generation check",
        "",
        "Every profile was generated twice during export; mismatches are validation failures.",
        "",
    ])
    if review.validation_failures:
        lines.extend(["## All validation failures", ""])
        lines.extend(f"- {item}" for item in review.validation_failures)
        lines.append("")
    return "\n".join(lines)


def write_generated_program_review(output_directory: Path) -> tuple[Path, Path, GeneratedProgramReview]:
    """Write deterministic review artefacts under ``output_directory``."""
    output_directory.mkdir(parents=True, exist_ok=True)
    review = build_generated_program_review()
    csv_path = output_directory / "stage7_generated_program_review.csv"
    summary_path = output_directory / "stage7_generated_program_review_summary.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(review.rows)
    summary_path.write_text(format_review_summary(review), encoding="utf-8")
    return csv_path, summary_path, review


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    csv_path, summary_path, review = write_generated_program_review(root / "tmp")
    print(f"CSV={csv_path}")
    print(f"SUMMARY={summary_path}")
    print(f"COMBINATIONS={review.combinations_count}")
    print(f"VALIDATION_FAILURES={len(review.validation_failures)}")
    return 0 if not review.validation_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
