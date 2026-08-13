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
    UserWorkoutPlanExercise,
    WorkoutTemplate,
    WorkoutTemplateDay,
    WorkoutTemplateExercise,
)
from services.workout_plans import (
    EXERCISE_DEFINITIONS,
    EXERCISE_ALTERNATIVES,
    TEMPLATE_DEFINITIONS,
    FitnessProfileRequiredError,
    LIMITATIONS_NOTICE,
    WorkoutPlanNotReadyError,
    assign_workout_plan,
    ensure_workout_catalog,
    format_workout_plan,
    format_workout_plan_preview,
    get_assigned_workout_plan,
    normalize_profile,
)


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
        self.assertEqual(len(TEMPLATE_DEFINITIONS), first.templates)
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

        self.assertEqual(
            "v2_fat_loss_some_experience_2_short_gym",
            template.code,
        )
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
        self.assertEqual(4, normalized.workouts_per_week)
        self.assertEqual("short", normalized.duration_bucket)
        self.assertEqual("gym", normalized.equipment)
        self.assertEqual(4, len(normalized.fallback_notes))

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
        self.assertIn("1. Тренировка 1", text)
        self.assertIn("×", text)
        self.assertIn("отдых", text)
        self.assertIn("Подсказка:", text)
        self.assertIn("Группа мышц:", text)
        self.assertIn("1. Тренировка 1 —", text)

    def test_day_title_uses_ordered_unique_groups_from_its_exercises(self) -> None:
        result = assign_workout_plan(self.user_id, self.database)
        text = format_workout_plan(result.plan)
        first_day = result.plan.days[0]
        groups = [item.primary_muscle_group for item in first_day.exercises]

        self.assertIn(
            f"1. Тренировка 1 — {', '.join(dict.fromkeys(groups))}",
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

    def test_experienced_profile_uses_deterministic_fallback(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.experience_level = "advanced"
            session.commit()

        result = assign_workout_plan(self.user_id, self.database)
        self.assertIn("ближайший доступный шаблон", " ".join(result.fallback_notes))

    def test_strength_waits_for_stage_seven_c_instead_of_wrong_plan(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "strength"
            profile.training_environment = "gym"
            session.commit()

        with self.assertRaises(WorkoutPlanNotReadyError):
            assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            count = session.scalar(select(func.count(UserWorkoutPlan.id)))
        self.assertEqual(0, count)

    def test_new_non_gym_profile_waits_for_stage_seven_c(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.training_environment = "home"
            session.commit()

        with self.assertRaises(WorkoutPlanNotReadyError):
            assign_workout_plan(self.user_id, self.database)

    def test_longest_supported_plan_fits_one_telegram_message(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.workouts_per_week = 4
            profile.session_duration_minutes = 60
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


if __name__ == "__main__":
    unittest.main()
