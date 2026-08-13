import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from db.migrations import DuplicateTelegramIdsError, run_migrations
from db.models import SqliteSession


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.sessions: list[SqliteSession] = []

    def tearDown(self) -> None:
        for session in self.sessions:
            session.dispose()
        self.temp_directory.cleanup()

    def make_session(self, filename: str = "test.db") -> SqliteSession:
        database = Path(self.temp_directory.name) / filename
        session = SqliteSession(sqlite_url(database))
        self.sessions.append(session)
        return session

    def test_empty_database_is_created_by_migrations(self) -> None:
        session = self.make_session()

        applied = run_migrations(session.engine)
        table_names = set(inspect(session.engine).get_table_names())

        self.assertEqual((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), applied)
        self.assertTrue(
            {
                "users",
                "payments",
                "fitness_profiles",
                "user_access",
                "exercises",
                "workout_templates",
                "workout_template_days",
                "workout_template_exercises",
                "user_workout_plans",
                "user_workout_plan_days",
                "user_workout_plan_exercises",
                "workout_sessions",
                "workout_session_exercises",
                "workout_set_results",
                "subscription_payments",
                "schema_migrations",
            }.issubset(table_names)
        )

    def test_migrations_are_safe_to_run_repeatedly(self) -> None:
        session = self.make_session()

        first_run = run_migrations(session.engine)
        second_run = run_migrations(session.engine)
        with session.engine.connect() as connection:
            applied_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM schema_migrations"
            ).scalar_one()

        self.assertEqual((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), first_run)
        self.assertEqual((), second_run)
        self.assertEqual(10, applied_count)

    def test_migration_three_preserves_stage_one_data(self) -> None:
        session = self.make_session("stage-one.db")
        self.assertEqual((1, 2), run_migrations(session.engine, target_version=2))
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 123456789, 'Test User', 'test_user', 0)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO fitness_profiles (
                    user_id, age, sex, height_cm, weight_kg, goal,
                    experience_level, workouts_per_week,
                    session_duration_minutes, limitations, completed_at
                ) VALUES (
                    1, 30, 'male', 180, 80.5, 'muscle_gain',
                    'beginner', 3, 60, NULL, '2026-08-09 12:00:00'
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO user_access (
                    user_id, trial_started_at, trial_ends_at
                ) VALUES (
                    1, '2026-08-09 12:00:00', '2026-08-12 12:00:00'
                )
                """
            )

        applied = run_migrations(session.engine)

        with session.engine.connect() as connection:
            profile = connection.exec_driver_sql(
                "SELECT goal, workouts_per_week FROM fitness_profiles WHERE user_id = 1"
            ).one()
            access = connection.exec_driver_sql(
                "SELECT trial_started_at, trial_ends_at FROM user_access WHERE user_id = 1"
            ).one()

        self.assertEqual((3, 4, 5, 6, 7, 8, 9, 10), applied)
        self.assertEqual(("muscle_gain", 3), profile)
        self.assertEqual(
            ("2026-08-09 12:00:00", "2026-08-12 12:00:00"),
            access,
        )

    def test_workout_planning_schema_has_required_constraints(self) -> None:
        session = self.make_session("planning-schema.db")
        run_migrations(session.engine)
        inspector = inspect(session.engine)

        plan_unique_constraints = inspector.get_unique_constraints(
            "user_workout_plans"
        )
        self.assertTrue(
            any(
                constraint["column_names"] == ["user_id"]
                for constraint in plan_unique_constraints
            )
        )

        with self.assertRaises(IntegrityError):
            with session.engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    INSERT INTO workout_templates (
                        code, name, goal, experience_level,
                        workouts_per_week, duration_bucket, equipment
                    ) VALUES (
                        'invalid', 'Invalid', 'muscle_gain', 'beginner',
                        5, 'standard', 'gym'
                    )
                    """
                )

    def test_migration_five_preserves_stage_one_and_two_data(self) -> None:
        session = self.make_session("stage-two.db")
        self.assertEqual(
            (1, 2, 3, 4),
            run_migrations(session.engine, target_version=4),
        )
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 123456789, 'Test User', 'test_user', 0)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO fitness_profiles (
                    user_id, age, sex, height_cm, weight_kg, goal,
                    experience_level, workouts_per_week,
                    session_duration_minutes, limitations, completed_at
                ) VALUES (
                    1, 30, 'male', 180, 80.5, 'muscle_gain',
                    'beginner', 1, 60, NULL, '2026-08-10 12:00:00'
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO user_access (
                    user_id, trial_started_at, trial_ends_at
                ) VALUES (
                    1, '2026-08-10 12:00:00', '2026-08-13 12:00:00'
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO exercises (
                    id, code, name, muscle_group, primary_muscle_group,
                    equipment, variant, alternative_name, hint, restriction_tags
                ) VALUES (
                    1, 'test_press', 'Тестовый жим', 'chest', 'грудь',
                    'machine', NULL, NULL, 'Без рывков.', ''
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workout_templates (
                    id, code, name, goal, experience_level,
                    workouts_per_week, duration_bucket, equipment
                ) VALUES (
                    1, 'test_template', 'Тестовый план', 'muscle_gain',
                    'beginner', 1, 'standard', 'gym'
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workout_template_days (id, template_id, day_number, title)
                VALUES (1, 1, 1, 'Тренировка 1')
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workout_template_exercises (
                    id, template_day_id, exercise_id, exercise_order,
                    sets, reps_min, reps_max, rest_seconds
                ) VALUES (1, 1, 1, 1, 3, 8, 12, 90)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO user_workout_plans (
                    id, user_id, template_id, profile_signature
                ) VALUES (1, 1, 1, 'test-signature')
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO user_workout_plan_days (id, plan_id, day_number, title)
                VALUES (1, 1, 1, 'Тренировка 1')
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO user_workout_plan_exercises (
                    id, plan_day_id, exercise_id, exercise_order,
                    exercise_name, primary_muscle_group, sets,
                    reps_min, reps_max, rest_seconds, hint
                ) VALUES (
                    1, 1, 1, 1, 'Тестовый жим', 'грудь',
                    3, 8, 12, 90, 'Без рывков.'
                )
                """
            )

        tables = (
            "users",
            "fitness_profiles",
            "user_access",
            "exercises",
            "workout_templates",
            "workout_template_days",
            "workout_template_exercises",
            "user_workout_plans",
            "user_workout_plan_days",
            "user_workout_plan_exercises",
        )
        with session.engine.connect() as connection:
            before = {
                table: connection.exec_driver_sql(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).fetchall()
                for table in tables
            }

        self.assertEqual(
            (5, 6),
            run_migrations(session.engine, target_version=6),
        )

        with session.engine.connect() as connection:
            after = {
                table: connection.exec_driver_sql(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).fetchall()
                for table in tables
            }
        self.assertEqual(before, after)

    def test_migration_seven_preserves_exercise_and_history_references(self) -> None:
        session = self.make_session("stage-seven.db")
        self.assertEqual(
            (1, 2, 3, 4, 5, 6),
            run_migrations(session.engine, target_version=6),
        )
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO users
                (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 7001, 'Catalog User', 'catalog_user', 0)"""
            )
            connection.exec_driver_sql(
                """INSERT INTO exercises (
                    id, code, name, muscle_group, primary_muscle_group,
                    equipment, hint, restriction_tags
                ) VALUES (
                    13, 'barbell_bench_press', 'Legacy bench', 'chest',
                    'грудь', 'barbell', 'Legacy hint', ''
                )"""
            )
            connection.exec_driver_sql(
                """INSERT INTO workout_sessions (
                    id, user_id, day_number, day_title, status,
                    started_at, finished_at
                ) VALUES (
                    1, 1, 1, 'Legacy day', 'completed',
                    '2026-08-01 10:00:00', '2026-08-01 11:00:00'
                )"""
            )
            connection.exec_driver_sql(
                """INSERT INTO workout_session_exercises (
                    id, session_id, exercise_order,
                    planned_exercise_id, planned_exercise_name,
                    planned_primary_muscle_group, planned_target_sets,
                    planned_target_reps_min, planned_target_reps_max,
                    planned_rest_seconds, planned_hint,
                    selected_exercise_id, selected_exercise_name,
                    selected_primary_muscle_group, selected_target_sets,
                    selected_target_reps_min, selected_target_reps_max,
                    selected_rest_seconds, selected_hint
                ) VALUES (
                    1, 1, 1, 13, 'Legacy bench', 'грудь', 1, 8, 12, 90,
                    'Legacy hint', 13, 'Legacy bench', 'грудь', 1, 8, 12,
                    90, 'Legacy hint'
                )"""
            )
            connection.exec_driver_sql(
                """INSERT INTO workout_set_results (
                    id, session_exercise_id, set_number,
                    actual_weight_kg, actual_reps
                ) VALUES (1, 1, 1, 60, 10)"""
            )

        with session.engine.connect() as connection:
            references_before = connection.exec_driver_sql(
                """SELECT e.id, wse.planned_exercise_id,
                    wse.selected_exercise_id, wsr.session_exercise_id,
                    wsr.actual_weight_kg, wsr.actual_reps
                FROM exercises e
                JOIN workout_session_exercises wse
                    ON wse.selected_exercise_id = e.id
                JOIN workout_set_results wsr
                    ON wsr.session_exercise_id = wse.id
                WHERE e.code = 'barbell_bench_press'"""
            ).one()

        self.assertEqual((7,), run_migrations(session.engine, target_version=7))
        self.assertEqual((), run_migrations(session.engine, target_version=7))

        inspector = inspect(session.engine)
        taxonomy_columns = {
            column["name"] for column in inspector.get_columns("exercises")
        }
        with session.engine.connect() as connection:
            references_after = connection.exec_driver_sql(
                """SELECT e.id, wse.planned_exercise_id,
                    wse.selected_exercise_id, wsr.session_exercise_id,
                    wsr.actual_weight_kg, wsr.actual_reps
                FROM exercises e
                JOIN workout_session_exercises wse
                    ON wse.selected_exercise_id = e.id
                JOIN workout_set_results wsr
                    ON wsr.session_exercise_id = wse.id
                WHERE e.code = 'barbell_bench_press'"""
            ).one()
            taxonomy = connection.exec_driver_sql(
                """SELECT muscle_group, secondary_muscle_groups,
                    training_environments, experience_levels,
                    movement_pattern, progression_type, equivalence_group
                FROM exercises WHERE id = 13"""
            ).one()

        self.assertTrue(
            {
                "secondary_muscle_groups", "training_environments",
                "experience_levels", "movement_pattern", "progression_type",
                "equivalence_group",
            }.issubset(taxonomy_columns)
        )
        self.assertEqual(references_before, references_after)
        self.assertEqual(
            (
                "chest", "triceps,shoulders", "gym,functional_gym",
                "intermediate,advanced", "horizontal_push",
                "external_load_reps", "horizontal_chest_press",
            ),
            taxonomy,
        )

    def test_migration_eight_maps_legacy_profile_without_inventing_place(self) -> None:
        session = self.make_session("stage-seven-profile.db")
        self.assertEqual(
            (1, 2, 3, 4, 5, 6, 7),
            run_migrations(session.engine, target_version=7),
        )
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO users
                (id, tg_id, fullname, username, inviter_id)
                VALUES
                (1, 8101, 'Intermediate User', 'intermediate_user', 0),
                (2, 8102, 'Advanced User', 'advanced_user', 0)"""
            )
            connection.exec_driver_sql(
                """INSERT INTO fitness_profiles (
                    user_id, age, sex, height_cm, weight_kg, goal,
                    experience_level, workouts_per_week,
                    session_duration_minutes, limitations, completed_at,
                    updated_at
                ) VALUES
                (1, 30, 'male', 180, 80, 'muscle_gain',
                 'some_experience', 3, 60, NULL,
                 '2026-08-09 12:00:00', '2026-08-10 12:00:00'),
                (2, 32, 'female', 170, 65, 'fat_loss',
                 'experienced', 4, 45, 'Legacy limitation',
                 '2026-08-09 13:00:00', '2026-08-10 13:00:00')"""
            )
            connection.exec_driver_sql(
                """INSERT INTO user_access (user_id, trial_ends_at)
                VALUES (1, '2026-08-20 12:00:00'),
                       (2, '2026-08-21 13:00:00')"""
            )

        with session.engine.connect() as connection:
            access_before = connection.exec_driver_sql(
                "SELECT * FROM user_access ORDER BY user_id"
            ).fetchall()

        self.assertEqual((8, 9, 10), run_migrations(session.engine))
        self.assertEqual((), run_migrations(session.engine, target_version=9))

        with session.engine.connect() as connection:
            profiles = connection.exec_driver_sql(
                """SELECT user_id, goal, experience_level,
                    training_environment, workouts_per_week,
                    session_duration_minutes, limitations, completed_at,
                    updated_at FROM fitness_profiles ORDER BY user_id"""
            ).fetchall()
            access_after = connection.exec_driver_sql(
                "SELECT * FROM user_access ORDER BY user_id"
            ).fetchall()

        self.assertEqual("intermediate", profiles[0].experience_level)
        self.assertEqual("advanced", profiles[1].experience_level)
        self.assertIsNone(profiles[0].training_environment)
        self.assertIsNone(profiles[1].training_environment)
        self.assertEqual("muscle_gain", profiles[0].goal)
        self.assertEqual("fat_loss", profiles[1].goal)
        self.assertEqual(access_before, access_after)

    def test_migration_nine_adds_nullable_validated_progression_snapshots(self) -> None:
        session = self.make_session("stage-seven-progression.db")
        self.assertEqual(
            (1, 2, 3, 4, 5, 6, 7, 8),
            run_migrations(session.engine, target_version=8),
        )

        self.assertEqual((9,), run_migrations(session.engine, target_version=9))
        self.assertEqual((), run_migrations(session.engine, target_version=9))

        inspector = inspect(session.engine)
        plan_columns = {
            column["name"]
            for column in inspector.get_columns("user_workout_plan_exercises")
        }
        snapshot_columns = {
            column["name"]
            for column in inspector.get_columns("workout_session_exercises")
        }
        self.assertIn("progression_strategy", plan_columns)
        self.assertTrue(
            {
                "planned_progression_strategy",
                "selected_progression_strategy",
            }.issubset(snapshot_columns)
        )

        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO users
                (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 9001, 'Migration User', 'migration_user', 0)"""
            )
            connection.exec_driver_sql(
                """INSERT INTO workout_sessions (
                    id, user_id, day_number, day_title, status, started_at
                ) VALUES (1, 1, 1, 'Day', 'in_progress', '2026-08-13')"""
            )
            connection.exec_driver_sql(
                """INSERT INTO workout_session_exercises (
                    id, session_id, exercise_order,
                    planned_exercise_name, planned_primary_muscle_group,
                    planned_target_sets, planned_target_reps_min,
                    planned_target_reps_max, planned_rest_seconds, planned_hint,
                    selected_exercise_name, selected_primary_muscle_group,
                    selected_target_sets, selected_target_reps_min,
                    selected_target_reps_max, selected_rest_seconds, selected_hint,
                    planned_progression_strategy, selected_progression_strategy
                ) VALUES (
                    1, 1, 1, 'planned', 'test', 3, 8, 12, 90, '',
                    'selected', 'test', 3, 8, 12, 90, '',
                    'strength_load_reps', 'bodyweight_reps'
                )"""
            )
        with self.assertRaises(IntegrityError):
            with session.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE workout_session_exercises "
                    "SET selected_progression_strategy = 'timed_conditioning' "
                    "WHERE id = 1"
                )

    def test_migration_ten_adds_durable_format_blocks_without_touching_legacy_rows(self) -> None:
        session = self.make_session("stage-seven-formats.db")
        run_migrations(session.engine, target_version=9)
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users (id, tg_id, fullname, username, inviter_id) "
                "VALUES (1, 9100, 'Formats User', 'formats', 0)"
            )
            before = connection.exec_driver_sql("SELECT * FROM users").fetchall()

        self.assertEqual((10,), run_migrations(session.engine))
        self.assertEqual((), run_migrations(session.engine))
        inspector = inspect(session.engine)
        self.assertTrue({
            "user_workout_plan_blocks", "workout_session_blocks",
            "workout_format_interval_results",
        }.issubset(inspector.get_table_names()))
        with session.engine.begin() as connection:
            self.assertEqual(before, connection.exec_driver_sql("SELECT * FROM users").fetchall())
            versions = connection.exec_driver_sql(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).scalars().all()
        self.assertEqual(list(range(1, 11)), versions)

    def test_fitness_profile_and_access_schema(self) -> None:
        session = self.make_session()
        run_migrations(session.engine)
        inspector = inspect(session.engine)

        profile_columns = {
            column["name"]: column for column in inspector.get_columns(
                "fitness_profiles"
            )
        }
        access_columns = {
            column["name"]: column for column in inspector.get_columns(
                "user_access"
            )
        }

        self.assertEqual(
            {
                "user_id", "age", "sex", "height_cm", "weight_kg", "goal",
                "experience_level", "workouts_per_week",
                "training_environment",
                "session_duration_minutes", "limitations", "completed_at",
                "updated_at",
            },
            set(profile_columns),
        )
        self.assertEqual("FLOAT", str(profile_columns["weight_kg"]["type"]))
        self.assertTrue(profile_columns["limitations"]["nullable"])
        self.assertEqual(
            {
                "user_id", "trial_started_at", "trial_ends_at",
                "subscription_started_at", "subscription_ends_at", "updated_at",
            },
            set(access_columns),
        )
        self.assertNotIn("status", access_columns)
        self.assertTrue(access_columns["subscription_started_at"]["nullable"])
        self.assertTrue(access_columns["subscription_ends_at"]["nullable"])
        self.assertTrue(access_columns["trial_started_at"]["nullable"])
        self.assertTrue(access_columns["trial_ends_at"]["nullable"])

        user_indexes = inspector.get_indexes("users")
        unique_tg_id = [
            index for index in user_indexes
            if index["column_names"] == ["tg_id"] and index["unique"]
        ]
        self.assertEqual(1, len(unique_tg_id))

    def test_foreign_keys_are_enabled_for_connections(self) -> None:
        session = self.make_session()
        run_migrations(session.engine)

        with session.engine.connect() as connection:
            enabled = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one()
        self.assertEqual(1, enabled)

        with self.assertRaises(IntegrityError):
            with session.engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    INSERT INTO user_access (
                        user_id, trial_started_at, trial_ends_at
                    ) VALUES (999, '2026-01-01 00:00:00', '2026-01-04 00:00:00')
                    """
                )

    def test_duplicate_tg_ids_stop_unique_migration(self) -> None:
        session = self.make_session()
        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    tg_id BIGINT NOT NULL,
                    fullname TEXT NOT NULL,
                    username TEXT NOT NULL,
                    inviter_id BIGINT NOT NULL,
                    reg_datetime DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO users (tg_id, fullname, username, inviter_id)
                VALUES (100, 'First', 'first', 0),
                       (100, 'Second', 'second', 0)
                """
            )

        with self.assertRaises(DuplicateTelegramIdsError):
            run_migrations(session.engine)

        inspector = inspect(session.engine)
        self.assertNotIn("fitness_profiles", inspector.get_table_names())
        self.assertFalse(
            any(index["unique"] for index in inspector.get_indexes("users"))
        )

    def test_legacy_database_migration_preserves_user_data(self) -> None:
        session = self.make_session("legacy.db")

        with session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    tg_id BIGINT NOT NULL,
                    fullname TEXT NOT NULL,
                    username TEXT NOT NULL,
                    inviter_id BIGINT NOT NULL,
                    reg_datetime DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE payments (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user INTEGER NOT NULL REFERENCES users (id),
                    yoo_id TEXT NOT NULL,
                    link TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reg_datetime DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO users (
                    id, tg_id, fullname, username, inviter_id, reg_datetime
                ) VALUES (
                    1, 123456789, 'Test User', 'test_user', 0,
                    '2026-01-01 12:00:00'
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO payments (
                    id, user, yoo_id, link, status, reg_datetime
                ) VALUES (
                    1, 1, 'test-payment', 'https://example.invalid/payment',
                    'pending', '2026-01-01 12:05:00'
                )
                """
            )

        with session.engine.connect() as connection:
            users_before = connection.exec_driver_sql(
                "SELECT * FROM users ORDER BY id"
            ).fetchall()
            payments_before = connection.exec_driver_sql(
                "SELECT * FROM payments ORDER BY id"
            ).fetchall()

        applied = run_migrations(session.engine)

        with session.engine.connect() as connection:
            users_after = connection.exec_driver_sql(
                "SELECT * FROM users ORDER BY id"
            ).fetchall()
            payments_after = connection.exec_driver_sql(
                "SELECT * FROM payments ORDER BY id"
            ).fetchall()

        self.assertEqual((1, 2, 3, 4, 5, 6, 7, 8, 9, 10), applied)
        self.assertEqual(users_before, users_after)
        self.assertEqual(payments_before, payments_after)

    def test_create_all_uses_synchronous_sqlalchemy_api(self) -> None:
        session = self.make_session()

        result = session.create_all()

        self.assertIsNone(result)
        self.assertIn("fitness_profiles", inspect(session.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()
