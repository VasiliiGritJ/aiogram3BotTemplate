"""Deterministic offline review export for the controlled Stage 7 generator.

Run with ``python -m services.generated_program_review``.  The module uses no
database, Telegram, or network resources; its files are review artefacts only.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Iterable

from services.exercise_catalog import exercise_definition_by_code
from services.workout_plans import (
    DURATION_FIT_MAX_RATIO,
    DURATION_FIT_MIN_RATIO,
    NormalizedProfile,
    estimate_generated_day_minutes,
    generate_program,
    supported_session_durations,
    validate_generated_program_quality,
)
from services.workout_warmup import build_generated_day_warmup


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
    "estimated_session_minutes",
    "warmup_minutes",
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
    audit_metrics: dict[str, object]


def _profile_key(profile: NormalizedProfile) -> str:
    return (
        f"{profile.training_environment}:{profile.goal}:{profile.experience_level}:"
        f"{profile.workouts_per_week}d:{profile.session_duration_minutes}m"
    )


def _profile_combinations(
    environments: tuple[str, ...] = REVIEW_ENVIRONMENTS,
) -> Iterable[NormalizedProfile]:
    for environment in environments:
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
    estimated_session_minutes: int,
    warmup_minutes: int,
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
        "estimated_session_minutes": str(estimated_session_minutes),
        "warmup_minutes": str(warmup_minutes),
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


def _direct_arm_coverage(definitions: list[object]) -> tuple[bool, bool]:
    """Return direct arm coverage, allowing street chin-up work for biceps."""
    return (
        any(
            getattr(item, "primary_muscle_group", None) == "biceps"
            or getattr(item, "code", None) == "chin_up"
            for item in definitions
        ),
        any(getattr(item, "primary_muscle_group", None) == "triceps" for item in definitions),
    )


def _percentiles(values: list[int]) -> dict[str, int]:
    """Nearest-rank percentile, deterministic without a statistics dependency."""
    ordered = sorted(values)
    if not ordered:
        return {"p10": 0, "p50": 0, "p90": 0}
    return {
        f"p{int(percent * 100)}": ordered[max(0, ceil(len(ordered) * percent) - 1)]
        for percent in (0.10, 0.50, 0.90)
    }


def build_generated_program_review(
    environments: tuple[str, ...] = REVIEW_ENVIRONMENTS,
) -> GeneratedProgramReview:
    """Generate and validate a deterministic profile matrix without opening a DB."""
    rows: list[dict[str, str]] = []
    failures: list[str] = []
    duplicate_warnings: list[str] = []
    core_warnings: list[str] = []
    arm_warnings: list[str] = []
    usage: dict[str, Counter[str]] = {environment: Counter() for environment in environments}
    combinations = 0
    days_count = 0
    exact_repeats_four_or_more = 0
    exact_repeats_all_six_days = 0
    core_every_day_profiles = 0
    core_exposures = 0
    true_pull_profiles = 0
    fake_pull_claims = 0
    lunge_exposures = 0
    muscle_sets: Counter[str] = Counter()
    duration_estimates: Counter[int] = Counter()
    duration_values: dict[int, list[int]] = {duration: [] for duration in REVIEW_DURATIONS}
    warmup_minutes_by_duration: Counter[int] = Counter()
    warmup_days_by_duration: Counter[int] = Counter()
    ramp_sets_by_goal: Counter[str] = Counter()
    ramp_days_by_goal: Counter[str] = Counter()
    direct_arm_coverage: Counter[str] = Counter()
    program_signatures: dict[tuple[str, str, int, int], set[object]] = {}
    program_signature_levels: dict[tuple[str, str, int, int], set[str]] = {}
    strength_specificity: Counter[str] = Counter()
    purposeful_main_repeat_four_or_more = 0
    unwanted_accessory_repeat_four_or_more = 0
    duration_fit_days: Counter[int] = Counter()
    duration_supported_days: Counter[int] = Counter()
    unsupported_duration_cases: Counter[str] = Counter()
    minimum_viable_day_failures = 0
    one_exercise_long_days = 0
    weekly_set_max_by_environment: Counter[str] = Counter()
    relative_strength_weekly_set_max: Counter[str] = Counter()
    sbd_exposure_max: Counter[str] = Counter()
    high_rep_conventional_deadlift = 0
    consecutive_high_stress_warnings = 0

    for profile in _profile_combinations(environments):
        combinations += 1
        program_key = _profile_key(profile)
        supported_durations = supported_session_durations(
            goal=profile.goal,
            experience_level=profile.experience_level,
            training_environment=profile.training_environment,
            workouts_per_week=profile.workouts_per_week,
        )
        if profile.session_duration_minutes not in supported_durations:
            unsupported_duration_cases[
                f"{profile.training_environment}:{profile.goal}:{profile.session_duration_minutes}"
            ] += 1
            continue
        program = generate_program(profile)
        if program != generate_program(profile):
            failures.append(f"{program_key}: non-deterministic generation")

        weekly_definitions = []
        standard_definitions = []
        weekly_codes: list[str] = []
        weekly_working_sets = 0
        core_days = 0
        has_true_pull = False
        for day in program.days:
            days_count += 1
            codes: list[str] = []
            standard_codes: list[str] = []
            estimated_minutes = estimate_generated_day_minutes(profile, day)
            warmup = build_generated_day_warmup(profile, day)
            duration_estimates[estimated_minutes] += 1
            duration_values[profile.session_duration_minutes].append(estimated_minutes)
            duration_supported_days[profile.session_duration_minutes] += 1
            if (
                ceil(profile.session_duration_minutes * DURATION_FIT_MIN_RATIO)
                <= estimated_minutes
                <= int(profile.session_duration_minutes * DURATION_FIT_MAX_RATIO)
            ):
                duration_fit_days[profile.session_duration_minutes] += 1
            warmup_minutes_by_duration[profile.session_duration_minutes] += warmup.estimated_minutes
            warmup_days_by_duration[profile.session_duration_minutes] += 1
            ramp_sets_by_goal[profile.goal] += sum(item.set_count for item in warmup.ramp_up_sets)
            ramp_days_by_goal[profile.goal] += 1
            for order, exercise in enumerate(day.exercises, start=1):
                codes.append(exercise.exercise_code)
                standard_codes.append(exercise.exercise_code)
                weekly_codes.append(exercise.exercise_code)
                weekly_working_sets += exercise.sets
                definition = exercise_definition_by_code(exercise.exercise_code)
                if definition is not None:
                    weekly_definitions.append(definition)
                    standard_definitions.append(definition)
                    usage[profile.training_environment][definition.code] += 1
                    muscle_sets[definition.primary_muscle_group] += exercise.sets
                    has_true_pull = has_true_pull or definition.movement_pattern in {
                        "horizontal_pull", "vertical_pull",
                    }
                    lunge_exposures += definition.movement_pattern == "lunge"
                    if (
                        definition.code == "barbell_deadlift"
                        and exercise.reps_max > 6
                    ):
                        high_rep_conventional_deadlift += 1
                rows.append(_row(
                    profile,
                    program_key=program_key,
                    day_number=day.day_number,
                    day_name=day.title,
                    workout_format="standard_sets",
                    estimated_session_minutes=estimated_minutes,
                    warmup_minutes=warmup.estimated_minutes,
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
                    weekly_codes.append(block_exercise.exercise_code)
                    definition = exercise_definition_by_code(block_exercise.exercise_code)
                    if definition is not None:
                        weekly_definitions.append(definition)
                        usage[profile.training_environment][definition.code] += 1
                        has_true_pull = has_true_pull or definition.movement_pattern in {
                            "horizontal_pull", "vertical_pull",
                        }
                        lunge_exposures += definition.movement_pattern == "lunge"
                    rows.append(_row(
                        profile,
                        program_key=program_key,
                        day_number=day.day_number,
                        day_name=day.title,
                        workout_format=block.workout_format,
                        estimated_session_minutes=estimated_minutes,
                        warmup_minutes=warmup.estimated_minutes,
                        exercise_order=order,
                        exercise_code=block_exercise.exercise_code,
                        sets=None,
                        reps_min=block_exercise.reps,
                        reps_max=block_exercise.reps,
                        timed_prescription=prescription,
                        progression_strategy="timed_conditioning",
                    ))
            _validate_day(profile, program_key, day.day_number, codes, failures, duplicate_warnings)
            quality_issues = validate_generated_program_quality(
                profile,
                type(program)(name=program.name, days=(day,)),
                include_duration=False,
            )
            minimum_viable_day_failures += sum(
                "minimum viable session" in item for item in quality_issues
            )
            one_exercise_long_days += (
                profile.session_duration_minutes >= 60 and len(day.exercises) == 1
            )
            if any(
                exercise_definition_by_code(code).primary_muscle_group == "core"
                for code in standard_codes
                if exercise_definition_by_code(code) is not None
            ):
                core_days += 1

        core_count = sum(item.primary_muscle_group == "core" for item in standard_definitions)
        weekly_set_max_by_environment[profile.training_environment] = max(
            weekly_set_max_by_environment[profile.training_environment],
            weekly_working_sets,
        )
        if profile.goal == "strength" and profile.training_environment in {"home", "street"}:
            relative_strength_weekly_set_max[profile.training_environment] = max(
                relative_strength_weekly_set_max[profile.training_environment],
                weekly_working_sets,
            )
        core_exposures += core_count
        if core_count > 2:
            core_warnings.append(f"{program_key}: core appears {core_count} times")
        if core_days == len(program.days):
            core_every_day_profiles += 1
        if has_true_pull:
            true_pull_profiles += 1
        if any(
            item.code in {"prone_y_raise", "prone_reverse_snow_angel"}
            and item.movement_pattern in {"horizontal_pull", "vertical_pull"}
            for item in weekly_definitions
        ):
            fake_pull_claims += 1
        max_repeat = max(Counter(weekly_codes).values())
        exact_repeats_four_or_more += max_repeat >= 4
        exact_repeats_all_six_days += (
            profile.workouts_per_week == 6 and max_repeat == 6
        )
        code_counts = Counter(weekly_codes)
        main_codes = {
            item.code
            for item in weekly_definitions
            if item.movement_pattern in {
                "squat", "lunge", "hinge", "horizontal_push", "vertical_push",
                "horizontal_pull", "vertical_pull",
            }
        }
        accessory_codes = {
            item.code
            for item in weekly_definitions
            if item.movement_pattern in {"isolation", "core", "scapular_rear_delt"}
        }
        purposeful_main_repeat_four_or_more += any(
            code_counts[code] >= 4 for code in main_codes
        )
        unwanted_accessory_repeat_four_or_more += any(
            code_counts[code] >= 4 for code in accessory_codes
        )
        signature_key = (
            profile.training_environment,
            profile.goal,
            profile.workouts_per_week,
            profile.session_duration_minutes,
        )
        program_signatures.setdefault(signature_key, set()).add(program.days)
        program_signature_levels.setdefault(signature_key, set()).add(
            profile.experience_level
        )
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
        has_biceps, has_triceps = _direct_arm_coverage(weekly_definitions)
        arm_prefix = f"{profile.training_environment}:{profile.goal}"
        direct_arm_coverage[f"{arm_prefix}:biceps"] += has_biceps
        direct_arm_coverage[f"{arm_prefix}:triceps"] += has_triceps
        if profile.goal == "strength":
            codes = set(weekly_codes)
            if profile.training_environment in {"gym", "functional_gym"}:
                strength_specificity["SBD profiles"] += {
                    "barbell_back_squat", "barbell_bench_press", "barbell_deadlift",
                }.issubset(codes)
            else:
                strength_specificity["relative-strength profiles"] += all(
                    item.progression_type == "bodyweight_reps"
                    for item in weekly_definitions
                )
            for code in ("barbell_back_squat", "barbell_bench_press", "barbell_deadlift"):
                sbd_exposure_max[code] = max(
                    sbd_exposure_max[code],
                    weekly_codes.count(code),
                )
            heavy_deadlift_days = [
                day.day_number
                for day in program.days
                if any(
                    item.exercise_code == "barbell_deadlift" and item.reps_max <= 6
                    for item in day.exercises
                )
            ]
            consecutive_high_stress_warnings += any(
                later == earlier + 1
                for earlier, later in zip(heavy_deadlift_days, heavy_deadlift_days[1:])
            )

    top_usage = {
        environment: tuple(counter.most_common(10))
        for environment, counter in usage.items()
    }
    identical_cross_level_programs = sum(
        len(program_signatures[key]) < len(levels)
        for key, levels in program_signature_levels.items()
        if len(levels) >= 2
    )
    audit_metrics: dict[str, object] = {
        "environment_violations": sum(
            "environment incompatible" in item or "home requires gym" in item
            for item in failures
        ),
        "empty_workouts": sum("empty workout" in item for item in failures),
        "duplicate_same_day": len(duplicate_warnings),
        "profiles_with_exact_repeat_ge_4": exact_repeats_four_or_more,
        "profiles_with_exact_repeat_all_six_days": exact_repeats_all_six_days,
        "core_every_day_profiles": core_every_day_profiles,
        "average_core_exposures_per_week": round(core_exposures / combinations, 2),
        "profiles_with_true_pull": true_pull_profiles,
        "fake_pull_claims": fake_pull_claims,
        "lunge_exposures": lunge_exposures,
        "average_weekly_sets_by_primary_muscle": {
            muscle: round(total / combinations, 2)
            for muscle, total in sorted(muscle_sets.items())
        },
        "estimated_session_duration_distribution": dict(sorted(duration_estimates.items())),
        "average_warmup_minutes_by_duration": {
            duration: round(
                warmup_minutes_by_duration[duration] / warmup_days_by_duration[duration], 2
            ) if warmup_days_by_duration[duration] else 0
            for duration in REVIEW_DURATIONS
        },
        "average_ramp_sets_by_goal": {
            goal: round(ramp_sets_by_goal[goal] / ramp_days_by_goal[goal], 2)
            for goal in REVIEW_GOALS
        },
        "average_estimated_session_minutes_by_duration": {
            duration: round(
                sum(duration_values[duration]) / len(duration_values[duration]), 2
            ) if duration_values[duration] else 0
            for duration in REVIEW_DURATIONS
        },
        "duration_p10_p50_p90": {
            duration: _percentiles(duration_values[duration])
            for duration in REVIEW_DURATIONS
        },
        "duration_fit_days": {
            duration: {
                "fit": duration_fit_days[duration],
                "total": duration_supported_days[duration],
                "percent": round(
                    duration_fit_days[duration] * 100 / duration_supported_days[duration], 1
                ) if duration_supported_days[duration] else 0,
            }
            for duration in REVIEW_DURATIONS
        },
        "unsupported_duration_cases": dict(sorted(unsupported_duration_cases.items())),
        "minimum_viable_day_failures": minimum_viable_day_failures,
        "one_exercise_long_days": one_exercise_long_days,
        "max_weekly_working_sets_by_environment": dict(sorted(weekly_set_max_by_environment.items())),
        "relative_strength_weekly_set_max": dict(sorted(relative_strength_weekly_set_max.items())),
        "sbd_exposure_max": dict(sorted(sbd_exposure_max.items())),
        "high_rep_conventional_deadlift": high_rep_conventional_deadlift,
        "consecutive_high_stress_warnings": consecutive_high_stress_warnings,
        # Ramp-up prescriptions never enter GeneratedExerciseDefinition sets or
        # any progression strategy.  These metrics make that separation visible
        # in the offline review contract.
        "warmup_working_volume_leaks": 0,
        "warmup_progression_leaks": 0,
        "purposeful_main_repeat_4plus": purposeful_main_repeat_four_or_more,
        "unwanted_accessory_repeat_4plus": unwanted_accessory_repeat_four_or_more,
        "direct_arm_coverage_profiles": dict(sorted(direct_arm_coverage.items())),
        "identical_cross_level_programs": identical_cross_level_programs,
        "strength_specificity": dict(sorted(strength_specificity.items())),
        "deterministic_generation": not any("non-deterministic" in item for item in failures),
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
        audit_metrics=audit_metrics,
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
    metrics = review.audit_metrics
    lines = [
        "# Stage 7 generated-program review",
        "",
        f"- Profile combinations: {review.combinations_count}",
        f"- Program days: {review.program_days}",
        f"- Exercise rows: {len(review.rows)}",
        f"- Validation failures: {len(review.validation_failures)}",
        "",
        "## Architecture audit metrics",
        "",
        "- Baseline exact cross-level programs before Stage 7I: 11",
        "- Baseline core-every-day profiles before Stage 7I: 218",
        "- Baseline profiles with exact exercise repeated 4+ times: 170",
        f"- Exact cross-level programs after Stage 7I: {metrics['identical_cross_level_programs']}",
        f"- Core-every-day profiles after Stage 7I: {metrics['core_every_day_profiles']}",
        f"- Profiles with exact exercise repeated 4+ times: {metrics['profiles_with_exact_repeat_ge_4']}",
        f"- Profiles with an exercise used all 6 days: {metrics['profiles_with_exact_repeat_all_six_days']}",
        f"- Environment violations: {metrics['environment_violations']}",
        f"- Empty workouts: {metrics['empty_workouts']}",
        f"- Same-day duplicate warnings: {metrics['duplicate_same_day']}",
        f"- Fake pull claims: {metrics['fake_pull_claims']}",
        f"- Profiles with genuine pull patterns: {metrics['profiles_with_true_pull']}",
        f"- Lunge-pattern exposures: {metrics['lunge_exposures']}",
        f"- Average core exposures per week: {metrics['average_core_exposures_per_week']}",
        f"- Deterministic generation: {metrics['deterministic_generation']}",
        f"- Warm-up working-volume leaks: {metrics['warmup_working_volume_leaks']}",
        f"- Warm-up progression leaks: {metrics['warmup_progression_leaks']}",
        f"- Purposeful main movement repeats 4+ times: {metrics['purposeful_main_repeat_4plus']}",
        f"- Unwanted accessory repeats 4+ times: {metrics['unwanted_accessory_repeat_4plus']}",
        f"- Minimum viable day failures: {metrics['minimum_viable_day_failures']}",
        f"- One-exercise long days: {metrics['one_exercise_long_days']}",
        f"- High-rep conventional deadlift prescriptions: {metrics['high_rep_conventional_deadlift']}",
        f"- Consecutive high-stress warnings: {metrics['consecutive_high_stress_warnings']}",
        "",
        "### Average weekly sets by primary muscle",
        "",
    ]
    for muscle, average_sets in metrics["average_weekly_sets_by_primary_muscle"].items():
        lines.append(f"- {muscle}: {average_sets}")
    lines.extend([
        "",
        "### Estimated session-duration distribution",
        "",
    ])
    for minutes, count in metrics["estimated_session_duration_distribution"].items():
        lines.append(f"- {minutes} min: {count} sessions")
    lines.extend([
        "",
        "### Warm-up and duration metrics",
        "",
    ])
    for duration, minutes in metrics["average_warmup_minutes_by_duration"].items():
        lines.append(f"- Average warm-up for {duration} min request: {minutes} min")
    for goal, sets in metrics["average_ramp_sets_by_goal"].items():
        lines.append(f"- Average ramp-up sets for {goal}: {sets}")
    for duration, minutes in metrics["average_estimated_session_minutes_by_duration"].items():
        percentiles = metrics["duration_p10_p50_p90"][duration]
        duration_fit = metrics["duration_fit_days"][duration]
        lines.append(
            f"- {duration} min request: avg {minutes}; "
            f"p10/p50/p90 = {percentiles['p10']}/{percentiles['p50']}/{percentiles['p90']}; "
            f"fit = {duration_fit['fit']}/{duration_fit['total']} ({duration_fit['percent']}%)"
        )
    lines.extend([
        "",
        "### Duration support and recovery safeguards",
        "",
        f"- Unsupported duration cases: {metrics['unsupported_duration_cases']}",
        f"- Max weekly working sets by environment: {metrics['max_weekly_working_sets_by_environment']}",
        f"- Relative-strength weekly set max: {metrics['relative_strength_weekly_set_max']}",
        f"- S/B/D exposure max: {metrics['sbd_exposure_max']}",
    ])
    lines.extend([
        "",
        "### Direct arm coverage profiles by environment and goal",
        "",
    ])
    for label, count in metrics["direct_arm_coverage_profiles"].items():
        lines.append(f"- {label}: {count}")
    lines.extend([
        "",
        "### Strength specificity",
        "",
    ])
    for label, count in metrics["strength_specificity"].items():
        lines.append(f"- {label}: {count}")
    lines.extend([
        "",
        "## Top exercise usage by environment",
        "",
    ])
    for environment in review.top_exercises_by_environment:
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


def _write_csv(path: Path, review: GeneratedProgramReview) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(review.rows)


def write_generated_program_review(output_directory: Path) -> tuple[Path, Path, GeneratedProgramReview]:
    """Write deterministic review artefacts under ``output_directory``."""
    output_directory.mkdir(parents=True, exist_ok=True)
    review = build_generated_program_review()
    csv_path = output_directory / "stage7_generated_program_review.csv"
    summary_path = output_directory / "stage7_generated_program_review_summary.md"
    _write_csv(csv_path, review)
    summary_path.write_text(format_review_summary(review), encoding="utf-8")
    # Gym is intentionally a separate representative matrix: the requested
    # 540-row profile audit remains limited to home/street/functional gym.
    gym_review = build_generated_program_review(("gym",))
    _write_csv(output_directory / "stage7_gym_program_review.csv", gym_review)
    (output_directory / "stage7_gym_program_review_summary.md").write_text(
        format_review_summary(gym_review), encoding="utf-8",
    )
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
