import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import (
    Exercise,
    FitnessProfile,
    SqliteSession,
    User,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanExercise,
    WorkoutTemplate,
    WorkoutTemplateDay,
    WorkoutTemplateExercise,
)
from services.workout_plans import (
    DURATION_EXERCISE_BUDGETS,
    EXERCISE_DEFINITIONS,
    EXERCISE_ALTERNATIVES,
    TEMPLATE_DEFINITIONS,
    FitnessProfileRequiredError,
    LIMITATIONS_NOTICE,
    assign_workout_plan,
    ensure_workout_catalog,
    format_workout_plan,
    format_workout_plan_preview,
    get_assigned_workout_plan,
    normalize_profile,
    generate_program,
    supported_session_durations,
    WorkoutDurationUnsupportedError,
    WorkoutStrengthProfileUnsupportedError,
)
from services.exercise_catalog import exercise_definition_by_code
from services.workout_progression import ProgressionStrategy


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class WorkoutPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database = Path(self.temp_directory.name) / "plans.db"
        self.database = SqliteSession(sqlite_url(database))
        run_migrations(self.database.engine)
        with self.database() as session:
            user = User(
                tg_id=123456789,
                fullname="Test User",
                username="test_user",
                inviter_id=0,
            )
            session.add(user)
            session.flush()
            self.user_id = user.id
            session.add(self.make_profile(self.user_id))
            session.commit()

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    @staticmethod
    def make_profile(user_id: int, **overrides) -> FitnessProfile:
        values = {
            "user_id": user_id,
            "age": 30,
            "sex": "male",
            "height_cm": 180,
            "weight_kg": 80.5,
            "goal": "muscle_gain",
            "experience_level": "beginner",
            "training_environment": None,
            "workouts_per_week": 3,
            "session_duration_minutes": 60,
            "limitations": None,
            "completed_at": datetime(2026, 8, 9, 12, 0),
            "updated_at": datetime(2026, 8, 9, 12, 0),
        }
        values.update(overrides)
        return FitnessProfile(**values)

    def test_controlled_catalog_is_idempotent_and_complete(self) -> None:
        first = ensure_workout_catalog(self.database)
        second = ensure_workout_catalog(self.database)

        self.assertEqual(first, second)
        self.assertEqual(len(EXERCISE_DEFINITIONS), first.exercises)
        self.assertEqual(len(TEMPLATE_DEFINITIONS) + 1, first.templates)
        self.assertEqual(80, first.template_days)
        self.assertEqual(400, first.template_exercises)

    def test_same_profile_returns_same_plan_without_duplicate(self) -> None:
        first = assign_workout_plan(self.user_id, self.database)
        repeated = assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            plan_count = session.scalar(select(func.count(UserWorkoutPlan.id)))

        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        self.assertEqual(first.plan, repeated.plan)
        self.assertEqual(1, plan_count)

    def test_catalog_version_signature_rebuilds_future_generated_plan_names(self) -> None:
        first = assign_workout_plan(self.user_id, self.database)
        with self.database() as session:
            plan = session.get(UserWorkoutPlan, first.plan.id)
            plan.profile_signature = "legacy-catalog-v4"
            first_item = session.scalar(
                select(UserWorkoutPlanExercise)
                .join(
                    UserWorkoutPlanDay,
                    UserWorkoutPlanDay.id
                    == UserWorkoutPlanExercise.plan_day_id,
                )
                .where(UserWorkoutPlanDay.plan_id == plan.id)
                .order_by(UserWorkoutPlanExercise.id)
            )
            first_item.exercise_name = "Старое имя из snapshot"
            session.commit()

        refreshed = assign_workout_plan(self.user_id, self.database)
        names = {
            item.name
            for day in refreshed.plan.days
            for item in day.exercises
        }

        self.assertTrue(refreshed.created)
        self.assertNotEqual(first.plan.id, refreshed.plan.id)
        self.assertNotIn("Старое имя из snapshot", names)

    def test_supported_profile_selects_exact_template(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "fat_loss"
            profile.experience_level = "intermediate"
            profile.workouts_per_week = 2
            profile.session_duration_minutes = 40
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            plan = session.get(UserWorkoutPlan, result.plan.id)
            template = session.get(WorkoutTemplate, plan.template_id)

        self.assertEqual("v4_adaptive_formats", template.code)
        self.assertEqual(2, len(result.plan.days))
        self.assertTrue(
            all(len(day.exercises) == 4 for day in result.plan.days)
        )

    def test_unknown_values_use_deterministic_fallback(self) -> None:
        profile = self.make_profile(
            self.user_id,
            goal="legacy_goal",
            experience_level="legacy_experience",
            workouts_per_week=7,
            session_duration_minutes=None,
        )

        normalized = normalize_profile(profile)

        self.assertEqual("muscle_gain", normalized.goal)
        self.assertEqual("beginner", normalized.experience_level)
        self.assertEqual(6, normalized.workouts_per_week)
        self.assertEqual(45, normalized.session_duration_minutes)
        self.assertEqual("gym", normalized.equipment)
        self.assertEqual(5, len(normalized.fallback_notes))

    def test_limitations_do_not_filter_controlled_exercises(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.limitations = "Есть ограничение по колену"
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)

        self.assertTrue(result.plan.days)
        self.assertIn(LIMITATIONS_NOTICE, result.fallback_notes)

    def test_arbitrary_limitation_does_not_block_plan(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.limitations = "Непонятное индивидуальное ограничение"
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)

        self.assertTrue(result.created)
        self.assertTrue(result.plan.days)
        self.assertIn(LIMITATIONS_NOTICE, result.fallback_notes)

    def test_absent_limitations_assign_plan_without_notice(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)

        self.assertTrue(result.created)
        self.assertNotIn(LIMITATIONS_NOTICE, result.fallback_notes)

    def test_profile_change_replaces_plan_without_active_duplicate(self) -> None:
        first = assign_workout_plan(self.user_id, self.database)
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.workouts_per_week = 2
            session.commit()

        changed = assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            plan_count = session.scalar(select(func.count(UserWorkoutPlan.id)))

        self.assertNotEqual(first.plan.id, changed.plan.id)
        self.assertEqual(2, len(changed.plan.days))
        self.assertEqual(1, plan_count)

    def test_beginner_gym_matrix_has_machine_cable_majority_and_no_barbell(self) -> None:
        for days in range(2, 7):
            for duration in (30, 60, 90):
                with self.subTest(days=days, duration=duration):
                    profile = normalize_profile(self.make_profile(
                        self.user_id,
                        training_environment="gym",
                        workouts_per_week=days,
                        session_duration_minutes=duration,
                    ))
                    if duration not in supported_session_durations(
                        goal=profile.goal,
                        experience_level=profile.experience_level,
                        training_environment=profile.training_environment,
                        workouts_per_week=profile.workouts_per_week,
                    ):
                        with self.assertRaises(WorkoutDurationUnsupportedError):
                            generate_program(profile)
                        continue
                    first = generate_program(profile)
                    second = generate_program(profile)
                    self.assertEqual(first, second)
                    all_definitions = []
                    for day in first.days:
                        definitions = [
                            exercise_definition_by_code(item.exercise_code)
                            for item in day.exercises
                        ]
                        all_definitions.extend(definitions)
                        self.assertFalse(any(
                            item.equipment == "barbell" for item in definitions
                        ))
                        self.assertNotIn(
                            "bodyweight_squat",
                            {item.code for item in definitions},
                        )
                    machine_cable = sum(
                        item.equipment in {"machine", "cable"}
                        for item in all_definitions
                    )
                    self.assertGreaterEqual(
                        machine_cable * 10,
                        len(all_definitions) * 7,
                    )

    def test_beginner_three_day_hypertrophy_has_balanced_weekly_coverage(self) -> None:
        profile = normalize_profile(self.make_profile(
            self.user_id,
            goal="muscle_gain",
            experience_level="beginner",
            training_environment="gym",
            workouts_per_week=3,
            session_duration_minutes=60,
        ))
        first = generate_program(profile)
        second = generate_program(profile)
        definitions = [
            exercise_definition_by_code(item.exercise_code)
            for day in first.days
            for item in day.exercises
        ]
        codes = [item.code for item in definitions]
        muscles = {item.primary_muscle_group for item in definitions}
        core_count = sum(item.primary_muscle_group == "core" for item in definitions)

        self.assertEqual(first, second)
        self.assertTrue({
            "chest", "back", "quads", "hamstrings", "glutes", "shoulders",
            "biceps", "triceps",
        }.issubset(muscles))
        self.assertGreaterEqual(core_count, 1)
        self.assertLessEqual(core_count, 2)
        self.assertLessEqual(max(codes.count(code) for code in codes), 2)
        self.assertTrue(any(
            item.primary_muscle_group == "biceps"
            and item.movement_pattern == "isolation"
            for item in definitions
        ))
        self.assertTrue(any(
            item.primary_muscle_group == "triceps"
            and item.movement_pattern == "isolation"
            for item in definitions
        ))

    def test_weekly_quality_matrix_bounds_core_and_repeated_exercises(self) -> None:
        for days in range(2, 7):
            for duration in (30, 60, 90):
                with self.subTest(days=days, duration=duration):
                    profile = normalize_profile(self.make_profile(
                        self.user_id,
                        goal="muscle_gain",
                        experience_level="beginner",
                        training_environment="gym",
                        workouts_per_week=days,
                        session_duration_minutes=duration,
                    ))
                    if duration not in supported_session_durations(
                        goal=profile.goal,
                        experience_level=profile.experience_level,
                        training_environment=profile.training_environment,
                        workouts_per_week=profile.workouts_per_week,
                    ):
                        with self.assertRaises(WorkoutDurationUnsupportedError):
                            generate_program(profile)
                        continue
                    program = generate_program(profile)
                    definitions = [
                        exercise_definition_by_code(item.exercise_code)
                        for day in program.days
                        for item in day.exercises
                    ]
                    core_count = sum(
                        item.primary_muscle_group == "core"
                        for item in definitions
                    )
                    counts = {
                        item.code: sum(other.code == item.code for other in definitions)
                        for item in definitions
                    }
                    self.assertLessEqual(core_count, 2)
                    self.assertLessEqual(max(counts.values()), 3)

    def test_advanced_gym_still_selects_free_weight_compounds(self) -> None:
        profile = normalize_profile(self.make_profile(
            self.user_id,
            experience_level="advanced",
            training_environment="gym",
        ))
        generated = generate_program(profile)
        definitions = [
            exercise_definition_by_code(item.exercise_code)
            for day in generated.days for item in day.exercises
        ]
        self.assertTrue(any(
            item.equipment in {"barbell", "dumbbell"}
            and item.movement_pattern in {
                "horizontal_push", "horizontal_pull", "squat", "hinge",
            }
            for item in definitions
        ))

    def test_saved_plan_contains_required_ordered_fields(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)
        loaded = get_assigned_workout_plan(self.user_id, self.database)

        self.assertEqual(result.plan, loaded)
        self.assertEqual([1, 2, 3], [day.day_number for day in loaded.days])
        for day in loaded.days:
            self.assertEqual(
                list(range(1, len(day.exercises) + 1)),
                [exercise.order for exercise in day.exercises],
            )
            for exercise in day.exercises:
                self.assertGreaterEqual(exercise.sets, 1)
                self.assertGreaterEqual(exercise.reps_min, 1)
                self.assertGreaterEqual(exercise.reps_max, exercise.reps_min)
                self.assertGreaterEqual(exercise.rest_seconds, 0)
                self.assertTrue(exercise.hint)

    def test_formatter_displays_all_required_fields(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)

        text = format_workout_plan(result.plan, result.fallback_notes)

        self.assertIn("Ваш недельный план", text)
        self.assertIn("1. Всё тело A", text)
        self.assertIn("×", text)
        self.assertIn("отдых", text)
        self.assertIn("Подсказка:", text)
        self.assertIn("Группа мышц:", text)
        self.assertIn("1. Всё тело A —", text)

    def test_day_title_uses_ordered_unique_groups_from_its_exercises(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)
        text = format_workout_plan(result.plan)
        first_day = result.plan.days[0]
        groups = [item.primary_muscle_group for item in first_day.exercises]

        self.assertIn(
            f"1. {first_day.title} — {', '.join(dict.fromkeys(groups))}",
            text,
        )

    def test_concrete_names_and_alternatives_are_controlled(self) -> None:
        names = {definition.code: definition.name for definition in EXERCISE_DEFINITIONS}

        self.assertEqual(
            "Горизонтальный жим сидя в рычажном тренажёре",
            names["chest_press"],
        )
        self.assertNotIn("Жим от груди в тренажёре", names.values())
        self.assertEqual(
            ("dumbbell_bench_press", "chest_press"),
            EXERCISE_ALTERNATIVES["barbell_bench_press"],
        )

    def test_exercise_metadata_and_preview_are_structured(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)
        with self.database() as session:
            exercise = session.scalar(
                select(Exercise).where(Exercise.code == "chest_press")
            )
            profile = session.get(FitnessProfile, self.user_id)

        preview = format_workout_plan_preview(result.plan, profile)
        self.assertEqual("грудь", exercise.primary_muscle_group)
        self.assertEqual("machine", exercise.equipment)
        self.assertEqual("горизонтальный", exercise.variant)
        self.assertIn("Мышцы:", preview)
        self.assertNotIn("Подсказка:", preview)
        self.assertNotIn("×", preview)

    def test_advanced_profile_uses_canonical_generation(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.experience_level = "advanced"
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)
        self.assertTrue(result.plan.days)

    def test_strength_generates_instead_of_wrong_legacy_plan(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "strength"
            profile.training_environment = "gym"
            profile.session_duration_minutes = 45
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)
        self.assertTrue(result.plan.days)

    def test_new_non_gym_profile_generates(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.training_environment = "home"
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)
        self.assertTrue(result.plan.days)

    def test_longest_supported_plan_fits_one_telegram_message(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.workouts_per_week = 4
            profile.session_duration_minutes = 45
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)
        text = format_workout_plan(result.plan, result.fallback_notes)

        self.assertLessEqual(len(text), 4096)

    def test_profile_is_required(self) -> None:
        with self.database() as session:
            session.delete(session.get(FitnessProfile, self.user_id))
            session.commit()

        with self.assertRaises(FitnessProfileRequiredError):
            assign_workout_plan(self.user_id, self.database)

    def test_catalog_relations_match_definition_counts(self) -> None:
        ensure_workout_catalog(self.database)
        with self.database() as session:
            day_count = session.scalar(select(func.count(WorkoutTemplateDay.id)))
            item_count = session.scalar(
                select(func.count(WorkoutTemplateExercise.id))
            )

        self.assertEqual(80, day_count)
        self.assertEqual(400, item_count)

    def test_all_stage_seven_profile_combinations_generate_valid_deterministic_programs(self) -> None:
        definitions = {item.code: item for item in EXERCISE_DEFINITIONS}
        count = 0
        unsupported = 0
        for goal in ("muscle_gain", "strength", "fat_loss"):
            for experience in ("beginner", "intermediate", "advanced"):
                for environment in ("gym", "functional_gym", "street", "home"):
                    for frequency in (2, 3, 4, 5, 6):
                        for duration in (30, 45, 60, 90):
                            with self.subTest(
                                goal=goal,
                                experience=experience,
                                environment=environment,
                                frequency=frequency,
                                duration=duration,
                            ):
                                profile = self.make_profile(
                                    self.user_id,
                                    goal=goal,
                                    experience_level=experience,
                                    training_environment=environment,
                                    workouts_per_week=frequency,
                                    session_duration_minutes=duration,
                                )
                                normalized = normalize_profile(profile)
                                supported = supported_session_durations(
                                    goal=normalized.goal,
                                    experience_level=normalized.experience_level,
                                    training_environment=normalized.training_environment,
                                    workouts_per_week=normalized.workouts_per_week,
                                )
                                if (
                                    goal == "strength"
                                    and environment == "home"
                                    and experience in {"intermediate", "advanced"}
                                ):
                                    self.assertEqual((), supported)
                                    with self.assertRaises(WorkoutStrengthProfileUnsupportedError):
                                        generate_program(normalized)
                                    unsupported += 1
                                    count += 1
                                    continue
                                if duration not in supported:
                                    with self.assertRaises(WorkoutDurationUnsupportedError):
                                        generate_program(normalized)
                                    unsupported += 1
                                    count += 1
                                    continue
                                first = generate_program(normalized)
                                second = generate_program(normalized)
                                self.assertEqual(first, second)
                                self.assertEqual(frequency, len(first.days))
                                for day in first.days:
                                    self.assertTrue(day.exercises)
                                    self.assertLessEqual(
                                        len(day.exercises),
                                        DURATION_EXERCISE_BUDGETS[duration],
                                    )
                                    codes = [item.exercise_code for item in day.exercises]
                                    self.assertEqual(len(codes), len(set(codes)))
                                    for code in codes:
                                        definition = definitions[code]
                                        self.assertIn(environment, definition.environments)
                                        self.assertIn(experience, definition.experience_levels)
                                        if environment == "home":
                                            self.assertEqual("bodyweight", definition.equipment)
                                count += 1
        self.assertEqual(720, count)
        self.assertGreater(unsupported, 0)

    def test_goal_and_experience_selection_priorities(self) -> None:
        beginner = generate_program(normalize_profile(self.make_profile(
            self.user_id, training_environment="gym", goal="muscle_gain",
            experience_level="beginner", workouts_per_week=3,
            session_duration_minutes=60,
        )))
        advanced = generate_program(normalize_profile(self.make_profile(
            self.user_id, training_environment="gym", goal="muscle_gain",
            experience_level="advanced", workouts_per_week=3,
            session_duration_minutes=60,
        )))
        strength = generate_program(normalize_profile(self.make_profile(
            self.user_id, training_environment="gym", goal="strength",
            experience_level="advanced", workouts_per_week=3,
            session_duration_minutes=60,
        )))
        lookup = {item.code: item for item in EXERCISE_DEFINITIONS}
        beginner_equipment = [lookup[item.exercise_code].equipment for day in beginner.days for item in day.exercises]
        advanced_equipment = [lookup[item.exercise_code].equipment for day in advanced.days for item in day.exercises]
        strength_main = [
            item for day in strength.days for item in day.exercises
            if item.reps_max <= 6
        ]
        strength_codes = {
            item.exercise_code for day in strength.days for item in day.exercises
        }
        self.assertGreater(
            sum(value in {"machine", "cable"} for value in beginner_equipment),
            sum(value in {"machine", "cable"} for value in advanced_equipment),
        )
        self.assertTrue(any(value == "barbell" for value in advanced_equipment))
        self.assertTrue(strength_main)
        self.assertTrue(
            {"barbell_back_squat", "barbell_bench_press", "barbell_deadlift"}
            .issubset(strength_codes)
        )

    def test_fat_loss_street_and_home_rules_remain_resistance_based(self) -> None:
        lookup = {item.code: item for item in EXERCISE_DEFINITIONS}
        for environment in ("street", "home"):
            with self.subTest(environment=environment):
                program = generate_program(normalize_profile(self.make_profile(
                    self.user_id,
                    goal="fat_loss",
                    experience_level="beginner",
                    training_environment=environment,
                    workouts_per_week=3,
                    session_duration_minutes=60,
                )))
                for day in program.days:
                    patterns = {
                        lookup[item.exercise_code].movement_pattern
                        for item in day.exercises
                    }
                    self.assertTrue(
                        patterns & {"squat", "lunge", "hinge", "horizontal_push", "horizontal_pull"}
                    )
                    for item in day.exercises:
                        definition = lookup[item.exercise_code]
                        self.assertIn(environment, definition.environments)
                        if environment == "home":
                            self.assertEqual("bodyweight", definition.equipment)

    def test_progression_strategy_routing_uses_goal_role_and_capability(self) -> None:
        lookup = {item.code: item for item in EXERCISE_DEFINITIONS}
        muscle_gain = generate_program(normalize_profile(self.make_profile(
            self.user_id,
            goal="muscle_gain",
            experience_level="advanced",
            training_environment="gym",
        )))
        strength = generate_program(normalize_profile(self.make_profile(
            self.user_id,
            goal="strength",
            experience_level="advanced",
            training_environment="gym",
        )))
        fat_loss_home = generate_program(normalize_profile(self.make_profile(
            self.user_id,
            goal="fat_loss",
            experience_level="beginner",
            training_environment="home",
        )))

        self.assertTrue(all(
            item.progression_strategy
            in {
                ProgressionStrategy.HYPERTROPHY_LOAD_REPS,
                ProgressionStrategy.BODYWEIGHT_REPS,
            }
            for day in muscle_gain.days for item in day.exercises
        ))
        for day in strength.days:
            self.assertEqual(
                ProgressionStrategy.STRENGTH_LOAD_REPS,
                day.exercises[0].progression_strategy,
            )
            self.assertTrue(all(
                item.progression_strategy
                != ProgressionStrategy.STRENGTH_LOAD_REPS
                for item in day.exercises[1:]
            ))
        self.assertTrue(all(
            item.progression_strategy == ProgressionStrategy.BODYWEIGHT_REPS
            for day in fat_loss_home.days for item in day.exercises
            if lookup[item.exercise_code].progression_type == "bodyweight_reps"
        ))

    def test_assigned_plan_persists_progression_strategy(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "strength"
            profile.experience_level = "beginner"
            profile.training_environment = "gym"
            profile.session_duration_minutes = 45
            session.commit()

        assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            stored = session.scalars(
                select(UserWorkoutPlanExercise)
                .order_by(
                    UserWorkoutPlanExercise.plan_day_id,
                    UserWorkoutPlanExercise.exercise_order,
                )
            ).all()
        self.assertTrue(stored)
        self.assertEqual(
            ProgressionStrategy.STRENGTH_LOAD_REPS,
            stored[0].progression_strategy,
        )

    def test_duration_preserves_main_priority_and_changes_volume(self) -> None:
        short = generate_program(normalize_profile(self.make_profile(
            self.user_id, training_environment="gym", workouts_per_week=2,
            session_duration_minutes=30,
        )))
        long = generate_program(normalize_profile(self.make_profile(
            self.user_id, training_environment="gym", workouts_per_week=2,
            session_duration_minutes=90,
        )))
        self.assertEqual(short.days[0].exercises[0].exercise_code, long.days[0].exercises[0].exercise_code)
        self.assertLess(len(short.days[0].exercises), len(long.days[0].exercises))

    def test_high_frequency_home_plan_persists_and_is_execution_readable(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "strength"
            profile.experience_level = "beginner"
            profile.training_environment = "home"
            profile.workouts_per_week = 6
            profile.session_duration_minutes = 30
            session.commit()

        assigned = assign_workout_plan(self.user_id, self.database)
        loaded = get_assigned_workout_plan(self.user_id, self.database)

        self.assertEqual(6, len(assigned.plan.days))
        self.assertEqual(assigned.plan, loaded)
        with self.database() as session:
            stored = session.scalars(select(UserWorkoutPlanExercise)).all()
        self.assertTrue(stored)
        self.assertTrue(all(item.sets >= 1 for item in stored))

    def test_functional_and_street_format_routing_is_deterministic_and_safe(self) -> None:
        lookup = {item.code: item for item in EXERCISE_DEFINITIONS}
        counts = {}
        for goal in ("muscle_gain", "strength", "fat_loss"):
            duration = {"muscle_gain": 60, "strength": 45, "fat_loss": 60}[goal]
            program = generate_program(normalize_profile(self.make_profile(
                self.user_id, goal=goal, experience_level="beginner",
                training_environment="functional_gym", workouts_per_week=3,
                session_duration_minutes=duration,
            )))
            counts[goal] = sum(len(day.blocks) for day in program.days)
            for day in program.days:
                for block in day.blocks:
                    self.assertTrue(block.exercises)
                    codes = [item.exercise_code for item in block.exercises]
                    self.assertEqual(len(codes), len(set(codes)))
                    self.assertFalse({"barbell_back_squat", "barbell_bench_press", "barbell_deadlift"} & set(codes))
                    for code in codes:
                        self.assertIn("beginner", lookup[code].experience_levels)
                        self.assertIn("functional_gym", lookup[code].environments)
        self.assertLessEqual(counts["strength"], counts["muscle_gain"])
        self.assertLess(counts["muscle_gain"], counts["fat_loss"])

        street = generate_program(normalize_profile(self.make_profile(
            self.user_id, goal="fat_loss", training_environment="street",
            workouts_per_week=3, session_duration_minutes=60,
        )))
        self.assertTrue(any(day.blocks for day in street.days))
        self.assertTrue(all(
            block.workout_format == "circuit_rounds"
            for day in street.days for block in day.blocks
        ))
        for day in street.days:
            for block in day.blocks:
                for item in block.exercises:
                    definition = lookup[item.exercise_code]
                    self.assertIn("street", definition.environments)
                    self.assertIn(definition.equipment, {"bodyweight", "pullup_dip_station"})

    def test_functional_workload_changes_with_duration_and_home_stays_standard(self) -> None:
        short = generate_program(normalize_profile(self.make_profile(
            self.user_id, goal="fat_loss", training_environment="functional_gym",
            session_duration_minutes=30,
        )))
        long = generate_program(normalize_profile(self.make_profile(
            self.user_id, goal="fat_loss", training_environment="functional_gym",
            session_duration_minutes=60,
        )))
        self.assertLess(
            short.days[0].blocks[0].duration_seconds,
            long.days[0].blocks[0].duration_seconds,
        )
        home = generate_program(normalize_profile(self.make_profile(
            self.user_id, goal="fat_loss", training_environment="home",
        )))
        self.assertTrue(all(not day.blocks for day in home.days))


if __name__ == "__main__":
    unittest.main()
