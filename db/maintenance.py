"""Operator-facing SQLite validation, backup, migration and restore helpers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from db.migrations import MIGRATIONS, run_migrations
from db.runtime import (
    SQLiteBackupReport,
    SQLiteOperationError,
    SQLiteValidation,
    create_sqlite_backup,
    create_sqlite_engine,
    restore_sqlite_backup,
    sqlite_url,
    validate_sqlite_database,
)


@dataclass(frozen=True)
class MigrationSafetyReport:
    backup: SQLiteBackupReport
    applied_versions: tuple[int, ...]
    validation: SQLiteValidation


def migrate_with_verified_backup(
    database_path: str | Path,
    backup_path: str | Path,
    *,
    writers_stopped: bool,
) -> MigrationSafetyReport:
    """Apply only pending migrations after an explicit, verified backup."""
    if not writers_stopped:
        raise SQLiteOperationError("Application writers must be stopped before migrations.")
    database = Path(database_path).resolve()
    before = validate_sqlite_database(database)
    if not before.is_valid:
        raise SQLiteOperationError("Source database integrity validation failed.")
    backup = create_sqlite_backup(database, backup_path)
    if not backup.source_unchanged:
        raise SQLiteOperationError("Source database changed during backup.")

    engine = create_sqlite_engine(sqlite_url(database))
    try:
        applied = run_migrations(engine)
    finally:
        engine.dispose()
    after = validate_sqlite_database(database)
    expected = tuple(migration.version for migration in MIGRATIONS)
    if not after.is_valid or after.migration_versions != expected:
        raise SQLiteOperationError(
            "Migration validation failed; services must remain stopped."
        )
    return MigrationSafetyReport(backup, applied, after)


def restore_to_new_destination(
    backup_path: str | Path,
    destination_path: str | Path,
) -> SQLiteBackupReport:
    """Restore only to a new destination; never overwrite an active database."""
    return restore_sqlite_backup(backup_path, destination_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SQLite maintenance without app secrets.")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--database", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--database", required=True)
    backup_parser.add_argument("--destination", required=True)

    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--database", required=True)
    migrate_parser.add_argument("--backup", required=True)
    migrate_parser.add_argument("--writers-stopped", action="store_true")

    restore_parser = subparsers.add_parser("restore-copy")
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--destination", required=True)

    arguments = parser.parse_args(argv)
    if arguments.operation == "validate":
        validation = validate_sqlite_database(arguments.database)
        if not validation.is_valid:
            raise SystemExit("Database validation failed.")
        print(f"validation=ok schema_version={validation.migration_versions[-1]}")
    elif arguments.operation == "backup":
        report = create_sqlite_backup(arguments.database, arguments.destination)
        print(f"backup=ok sha256={report.backup_sha256}")
    elif arguments.operation == "migrate":
        report = migrate_with_verified_backup(
            arguments.database,
            arguments.backup,
            writers_stopped=arguments.writers_stopped,
        )
        print(
            "migration=ok applied="
            + (",".join(map(str, report.applied_versions)) or "none")
        )
    else:
        report = restore_to_new_destination(arguments.backup, arguments.destination)
        print(f"restore=ok sha256={report.backup_sha256}")


if __name__ == "__main__":
    main()
