import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from db.migrations import run_migrations
from db.models import (
    Exercise,
    SqliteSession,
    User,
    UserAccess,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanExercise,
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSetResult,
    WorkoutTemplate,
    WorkoutTemplateDay,
    WorkoutTemplateExercise,
)


BASE_TIME = datetime(2026, 8, 10, 12, 0, 0)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class WorkoutExecutionSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database = Path(self.temp_directory.name) / "execution.db"
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
            session.commit()

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    def make_workout_session(
        self,
        *,
        status: str = "in_progress",
        finished_at: datetime | None = None,
        source_plan_id: int | None = None,
        source_plan_day_id: int | None = None,
    ) -> WorkoutSession:
        return WorkoutSession(
            user_id=self.user_id,
            source_plan_id=source_plan_id,
            source_plan_day_id=source_plan_day_id,
            day_number=1,
            day_title="Тренировка 1",
            status=status,
            started_at=BASE_TIME,
            finished_at=finished_at,
            updated_at=BASE_TIME,
        )

    @staticmethod
    def make_snapshot(
        session_id: int,
        *,
        planned_exercise_id: int | None = None,
        selected_exercise_id: int | None = None,
        source_plan_exercise_id: int | None = None,
    ) -> WorkoutSessionExercise:
        return WorkoutSessionExercise(
            session_id=session_id,
            source_plan_exercise_id=source_plan_exercise_id,
            exercise_order=1,
            planned_exercise_id=planned_exercise_id,
            planned_exercise_name="Жим штанги лёжа",
            planned_primary_muscle_group="грудь",
            planned_target_sets=3,
            planned_target_reps_min=8,
            planned_target_reps_max=12,
            planned_rest_seconds=90,
            planned_hint="Опускайте штангу подконтрольно.",
            selected_exercise_id=selected_exercise_id,
            selected_exercise_name="Жим гантелей лёжа",
            selected_primary_muscle_group="грудь",
            selected_target_sets=3,
            selected_target_reps_min=8,
            selected_target_reps_max=12,
            selected_rest_seconds=90,
            selected_hint="Держите кисти над локтями.",
        )

    @staticmethod
    def make_exercise(code: str, name: str) -> Exercise:
        return Exercise(
            code=code,
            name=name,
            muscle_group="chest",
            primary_muscle_group="грудь",
            equipment="free_weight",
            variant=None,
            alternative_name=None,
            hint="Контролируйте движение.",
            restriction_tags="",
        )

    def test_schema_has_exact_columns_indexes_and_delete_rules(self) -> None:
        inspector = inspect(self.database.engine)
        self.assertEqual(
            {
                "id", "user_id", "source_plan_id", "source_plan_day_id",
                "day_number", "day_title", "status", "started_at",
                "finished_at", "updated_at", "plan_source",
                "adaptation_mode",
            },
            {column["name"] for column in inspector.get_columns("workout_sessions")},
        )
        self.assertEqual(
            {
                "id", "session_id", "source_plan_exercise_id", "exercise_order",
                "planned_exercise_id", "planned_exercise_name",
                "planned_primary_muscle_group", "planned_target_sets",
                "planned_target_reps_min", "planned_target_reps_max",
                "planned_rest_seconds", "planned_hint", "selected_exercise_id",
                "selected_exercise_name", "selected_primary_muscle_group",
                "selected_target_sets", "selected_target_reps_min",
                "selected_target_reps_max", "selected_rest_seconds",
                "selected_hint", "planned_progression_strategy",
                "selected_progression_strategy",
                "session_block_id", "planned_format_reps",
                "selected_format_reps", "planned_station_order",
                "selected_station_order",
            },
            {
                column["name"]
                for column in inspector.get_columns("workout_session_exercises")
            },
        )
        self.assertEqual(
            {
                "id", "session_exercise_id", "set_number",
                "actual_weight_kg", "actual_reps", "completed_at",
            },
            {
                column["name"]
                for column in inspector.get_columns("workout_set_results")
            },
        )

        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("workout_sessions")
        }
        active_index = indexes["uq_workout_sessions_active_user"]
        self.assertTrue(active_index["unique"])
        self.assertEqual(["user_id"], active_index["column_names"])
        self.assertEqual(
            ["user_id", "finished_at"],
            indexes["ix_workout_sessions_user_finished_at"]["column_names"],
        )

        def foreign_keys(table: str) -> dict[str, tuple[str, str]]:
            with self.database.engine.connect() as connection:
                rows = connection.exec_driver_sql(
                    f"PRAGMA foreign_key_list({table})"
                ).mappings()
                return {
                    row["from"]: (row["table"], row["on_delete"])
                    for row in rows
                }

        self.assertEqual(
            {
                "user_id": ("users", "CASCADE"),
                "source_plan_id": ("user_workout_plans", "SET NULL"),
                "source_plan_day_id": ("user_workout_plan_days", "SET NULL"),
            },
            foreign_keys("workout_sessions"),
        )
        self.assertEqual(
            {
                "session_id": ("workout_sessions", "CASCADE"),
                "source_plan_exercise_id": (
                    "user_workout_plan_exercises", "SET NULL"
                ),
                "planned_exercise_id": ("exercises", "SET NULL"),
                "selected_exercise_id": ("exercises", "SET NULL"),
                "session_block_id": ("workout_session_blocks", "SET NULL"),
            },
            foreign_keys("workout_session_exercises"),
        )
        self.assertEqual(
            {
                "session_exercise_id": (
                    "workout_session_exercises", "CASCADE"
                )
            },
            foreign_keys("workout_set_results"),
        )

    def test_statuses_and_partial_unique_active_session_constraint(self) -> None:
        with self.database() as session:
            session.add(self.make_workout_session())
            session.commit()

        with self.assertRaises(IntegrityError):
            with self.database() as session:
                session.add(self.make_workout_session())
                session.commit()

        finished_at = BASE_TIME + timedelta(hours=1)
        with self.database() as session:
            session.add_all(
                (
                    self.make_workout_session(
                        status="completed", finished_at=finished_at
                    ),
                    self.make_workout_session(
                        status="cancelled", finished_at=finished_at
                    ),
                )
            )
            session.commit()
            session_count = session.scalar(select(func.count(WorkoutSession.id)))

        self.assertEqual(3, session_count)

        with self.assertRaises(IntegrityError):
            with self.database() as session:
                session.add(self.make_workout_session(status="unknown"))
                session.commit()

    def test_snapshot_keeps_planned_and_selected_values_separate(self) -> None:
        with self.database() as session:
            planned = self.make_exercise("planned_press", "Жим штанги лёжа")
            selected = self.make_exercise("selected_press", "Жим гантелей лёжа")
            session.add_all((planned, selected))
            session.flush()
            workout = self.make_workout_session()
            session.add(workout)
            session.flush()
            snapshot = self.make_snapshot(
                workout.id,
                planned_exercise_id=planned.id,
                selected_exercise_id=selected.id,
            )
            session.add(snapshot)
            session.flush()
            result = WorkoutSetResult(
                session_exercise_id=snapshot.id,
                set_number=1,
                actual_weight_kg=0,
                actual_reps=12,
                completed_at=BASE_TIME + timedelta(minutes=5),
            )
            session.add(result)
            session.commit()
            snapshot_id = snapshot.id

        with self.database() as session:
            stored = session.get(WorkoutSessionExercise, snapshot_id)
            self.assertNotEqual(stored.planned_exercise_id, stored.selected_exercise_id)
            self.assertEqual("Жим штанги лёжа", stored.planned_exercise_name)
            self.assertEqual("Жим гантелей лёжа", stored.selected_exercise_name)
            self.assertEqual(0, stored.set_results[0].actual_weight_kg)
            self.assertEqual(12, stored.set_results[0].actual_reps)

    def test_started_session_can_finish_after_trial_expires(self) -> None:
        trial_started_at = BASE_TIME - timedelta(days=2)
        trial_ends_at = BASE_TIME + timedelta(hours=1)
        finished_at = trial_ends_at + timedelta(hours=1)
        with self.database() as session:
            session.add(
                UserAccess(
                    user_id=self.user_id,
                    trial_started_at=trial_started_at,
                    trial_ends_at=trial_ends_at,
                    subscription_started_at=None,
                    subscription_ends_at=None,
                    updated_at=trial_started_at,
                )
            )
            workout = self.make_workout_session()
            session.add(workout)
            session.flush()
            snapshot = self.make_snapshot(workout.id)
            session.add(snapshot)
            session.flush()
            session.add(
                WorkoutSetResult(
                    session_exercise_id=snapshot.id,
                    set_number=1,
                    actual_weight_kg=20,
                    actual_reps=10,
                    completed_at=finished_at,
                )
            )
            workout.status = "completed"
            workout.finished_at = finished_at
            workout.updated_at = finished_at
            session.commit()

        with self.database() as session:
            stored_workout = session.get(WorkoutSession, workout.id)
            stored_access = session.get(UserAccess, self.user_id)
            self.assertEqual("completed", stored_workout.status)
            self.assertEqual(finished_at, stored_workout.finished_at)
            self.assertEqual(trial_started_at, stored_access.trial_started_at)
            self.assertEqual(trial_ends_at, stored_access.trial_ends_at)

    def test_set_result_constraints_and_unique_set_number(self) -> None:
        with self.database() as session:
            workout = self.make_workout_session()
            session.add(workout)
            session.flush()
            snapshot = self.make_snapshot(workout.id)
            session.add(snapshot)
            session.flush()
            snapshot_id = snapshot.id
            session.add(
                WorkoutSetResult(
                    session_exercise_id=snapshot_id,
                    set_number=1,
                    actual_weight_kg=10,
                    actual_reps=10,
                    completed_at=BASE_TIME,
                )
            )
            session.commit()

        invalid_values = (
            (1, 10, 10),
            (2, -0.5, 10),
            (2, 10, 0),
        )
        for set_number, weight, reps in invalid_values:
            with self.subTest(set_number=set_number, weight=weight, reps=reps):
                with self.assertRaises(IntegrityError):
                    with self.database() as session:
                        session.add(
                            WorkoutSetResult(
                                session_exercise_id=snapshot_id,
                                set_number=set_number,
                                actual_weight_kg=weight,
                                actual_reps=reps,
                                completed_at=BASE_TIME,
                            )
                        )
                        session.commit()

    def test_plan_and_exercise_deletion_preserve_history_snapshots(self) -> None:
        with self.database() as session:
            planned = self.make_exercise("planned", "Запланированное упражнение")
            selected = self.make_exercise("selected", "Выбранная альтернатива")
            session.add_all((planned, selected))
            session.flush()
            template = WorkoutTemplate(
                code="test_template",
                name="Тестовый шаблон",
                goal="muscle_gain",
                experience_level="beginner",
                workouts_per_week=1,
                duration_bucket="standard",
                equipment="gym",
            )
            session.add(template)
            session.flush()
            template_day = WorkoutTemplateDay(
                template_id=template.id,
                day_number=1,
                title="Тренировка 1",
            )
            session.add(template_day)
            session.flush()
            session.add(
                WorkoutTemplateExercise(
                    template_day_id=template_day.id,
                    exercise_id=planned.id,
                    exercise_order=1,
                    sets=3,
                    reps_min=8,
                    reps_max=12,
                    rest_seconds=90,
                )
            )
            plan = UserWorkoutPlan(
                user_id=self.user_id,
                template_id=template.id,
                profile_signature="snapshot-test",
                assigned_at=BASE_TIME,
                updated_at=BASE_TIME,
            )
            session.add(plan)
            session.flush()
            plan_day = UserWorkoutPlanDay(
                plan_id=plan.id,
                day_number=1,
                title="Тренировка 1",
            )
            session.add(plan_day)
            session.flush()
            plan_exercise = UserWorkoutPlanExercise(
                plan_day_id=plan_day.id,
                exercise_id=planned.id,
                exercise_order=1,
                exercise_name=planned.name,
                primary_muscle_group=planned.primary_muscle_group,
                sets=3,
                reps_min=8,
                reps_max=12,
                rest_seconds=90,
                hint=planned.hint,
            )
            session.add(plan_exercise)
            session.flush()
            workout = self.make_workout_session(
                source_plan_id=plan.id,
                source_plan_day_id=plan_day.id,
            )
            session.add(workout)
            session.flush()
            snapshot = self.make_snapshot(
                workout.id,
                source_plan_exercise_id=plan_exercise.id,
                planned_exercise_id=planned.id,
                selected_exercise_id=selected.id,
            )
            session.add(snapshot)
            session.flush()
            result = WorkoutSetResult(
                session_exercise_id=snapshot.id,
                set_number=1,
                actual_weight_kg=12.5,
                actual_reps=10,
                completed_at=BASE_TIME + timedelta(minutes=5),
            )
            session.add(result)
            session.commit()
            workout_id = workout.id
            snapshot_id = snapshot.id
            result_id = result.id

        with self.database() as session:
            session.delete(session.get(UserWorkoutPlan, plan.id))
            session.commit()
        with self.database() as session:
            session.delete(session.get(Exercise, selected.id))
            session.commit()

        with self.database() as session:
            stored_workout = session.get(WorkoutSession, workout_id)
            stored_snapshot = session.get(WorkoutSessionExercise, snapshot_id)
            stored_result = session.get(WorkoutSetResult, result_id)
            self.assertIsNotNone(stored_workout)
            self.assertIsNone(stored_workout.source_plan_id)
            self.assertIsNone(stored_workout.source_plan_day_id)
            self.assertIsNone(stored_snapshot.source_plan_exercise_id)
            self.assertIsNone(stored_snapshot.selected_exercise_id)
            self.assertEqual(
                "Жим гантелей лёжа",
                stored_snapshot.selected_exercise_name,
            )
            self.assertEqual(12.5, stored_result.actual_weight_kg)


if __name__ == "__main__":
    unittest.main()
