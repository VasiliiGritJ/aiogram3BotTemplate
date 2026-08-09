import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from db.migrations import DuplicateTelegramIdsError, run_migrations
from db.models import SqliteSession


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DATABASE = PROJECT_ROOT / "db.db"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

        self.assertEqual((1, 2), applied)
        self.assertTrue(
            {"users", "payments", "fitness_profiles", "user_access",
             "schema_migrations"}.issubset(table_names)
        )

    def test_migrations_are_safe_to_run_repeatedly(self) -> None:
        session = self.make_session()

        first_run = run_migrations(session.engine)
        second_run = run_migrations(session.engine)
        with session.engine.connect() as connection:
            applied_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM schema_migrations"
            ).scalar_one()

        self.assertEqual((1, 2), first_run)
        self.assertEqual((), second_run)
        self.assertEqual(2, applied_count)

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

    def test_existing_database_copy_preserves_user_data(self) -> None:
        real_database_hash = sha256(REAL_DATABASE)
        copied_database = Path(self.temp_directory.name) / "existing-copy.db"
        shutil.copy2(REAL_DATABASE, copied_database)
        session = SqliteSession(sqlite_url(copied_database))
        self.sessions.append(session)

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

        self.assertEqual((1, 2), applied)
        self.assertEqual(users_before, users_after)
        self.assertEqual(payments_before, payments_after)
        self.assertEqual(real_database_hash, sha256(REAL_DATABASE))

    def test_create_all_uses_synchronous_sqlalchemy_api(self) -> None:
        session = self.make_session()

        result = session.create_all()

        self.assertIsNone(result)
        self.assertIn("fitness_profiles", inspect(session.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()
