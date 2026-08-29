from pathlib import Path
import tempfile
import threading
import time
import unittest

from sqlalchemy import text

from db.maintenance import migrate_with_verified_backup, restore_to_new_destination
from db.migrations import MIGRATIONS, run_migrations
from db.models import SqliteSession
from db.runtime import (
    SQLiteOperationError,
    aggregate_table_counts,
    create_sqlite_backup,
    sqlite_url,
    validate_sqlite_database,
)


class SQLiteRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "active" / "runtime.db"
        self.database_path.parent.mkdir()
        self.database = SqliteSession(sqlite_url(self.database_path))
        run_migrations(self.database.engine)

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    def test_connection_policy_enables_fk_busy_timeout_and_wal(self) -> None:
        with self.database.engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        self.assertEqual(1, foreign_keys)
        self.assertEqual(5000, busy_timeout)
        self.assertEqual("wal", journal_mode.lower())

    def test_two_writers_wait_within_bound_and_reader_coexists(self) -> None:
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE runtime_contention (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )

        writer_started = threading.Event()
        release_writer = threading.Event()
        errors: list[BaseException] = []

        def first_writer() -> None:
            try:
                with self.database.engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO runtime_contention(value) VALUES ('first')")
                    )
                    writer_started.set()
                    release_writer.wait(timeout=2)
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=first_writer)
        first.start()
        self.assertTrue(writer_started.wait(timeout=2))

        with self.database.engine.connect() as connection:
            visible_during_write = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM runtime_contention"
            ).scalar_one()
        self.assertEqual(0, visible_during_write)

        second_finished = threading.Event()

        def second_writer() -> None:
            try:
                with self.database.engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO runtime_contention(value) VALUES ('second')")
                    )
                second_finished.set()
            except BaseException as error:
                errors.append(error)

        second = threading.Thread(target=second_writer)
        second.start()
        time.sleep(0.1)
        self.assertFalse(second_finished.is_set())
        release_writer.set()
        first.join(timeout=3)
        second.join(timeout=3)

        self.assertEqual([], errors)
        self.assertTrue(second_finished.is_set())
        with self.database.engine.connect() as connection:
            count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM runtime_contention"
            ).scalar_one()
        self.assertEqual(2, count)

    def test_backup_restore_preserves_integrity_schema_and_aggregates(self) -> None:
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(tg_id, fullname, username, inviter_id) "
                "VALUES (101, 'Review User', 'review', 0)"
            )
        expected_counts = aggregate_table_counts(
            self.database_path,
            ("users", "subscription_payments", "workout_sessions"),
        )
        backup_path = self.root / "backup" / "snapshot.db"
        report = create_sqlite_backup(self.database_path, backup_path)

        self.assertTrue(report.source_unchanged)
        self.assertTrue(report.validation.is_valid)
        self.assertEqual(
            tuple(migration.version for migration in MIGRATIONS),
            report.validation.migration_versions,
        )
        with self.assertRaises(SQLiteOperationError):
            create_sqlite_backup(self.database_path, backup_path)

        restored_path = self.root / "restore" / "restored.db"
        restore_report = restore_to_new_destination(backup_path, restored_path)
        restored_validation = validate_sqlite_database(restored_path)
        restored_counts = aggregate_table_counts(restored_path, expected_counts)
        self.assertTrue(restore_report.validation.is_valid)
        self.assertEqual(expected_counts, restored_counts)
        self.assertEqual(report.validation.migration_versions, restored_validation.migration_versions)

    def test_migration_helper_requires_stopped_writers_and_is_idempotent(self) -> None:
        backup_one = self.root / "migration-backup-one" / "snapshot.db"
        with self.assertRaises(SQLiteOperationError):
            migrate_with_verified_backup(
                self.database_path,
                backup_one,
                writers_stopped=False,
            )
        report = migrate_with_verified_backup(
            self.database_path,
            backup_one,
            writers_stopped=True,
        )
        self.assertEqual((), report.applied_versions)
        self.assertTrue(report.validation.is_valid)

        report_two = migrate_with_verified_backup(
            self.database_path,
            self.root / "migration-backup-two" / "snapshot.db",
            writers_stopped=True,
        )
        self.assertEqual((), report_two.applied_versions)
        self.assertEqual(report.validation.migration_versions, report_two.validation.migration_versions)


if __name__ == "__main__":
    unittest.main()
