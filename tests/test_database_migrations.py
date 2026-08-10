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

        self.assertEqual((1, 2, 3, 4), applied)
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

        self.assertEqual((1, 2, 3, 4), first_run)
        self.assertEqual((), second_run)
        self.assertEqual(4, applied_count)

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

        self.assertEqual((3, 4), applied)
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

        self.assertEqual((1, 2, 3, 4), applied)
        self.assertEqual(users_before, users_after)
        self.assertEqual(payments_before, payments_after)

    def test_create_all_uses_synchronous_sqlalchemy_api(self) -> None:
        session = self.make_session()

        result = session.create_all()

        self.assertIsNone(result)
        self.assertIn("fitness_profiles", inspect(session.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()
