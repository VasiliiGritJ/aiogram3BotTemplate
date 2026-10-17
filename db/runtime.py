"""Central SQLite runtime, validation, backup and restore primitives."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager, closing
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 5.0
DEFAULT_JOURNAL_MODE = "WAL"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLiteOperationError(RuntimeError):
    """Raised when a maintenance operation cannot complete safely."""


@dataclass(frozen=True)
class SQLitePolicy:
    journal_mode: str = DEFAULT_JOURNAL_MODE
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    connection_timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.journal_mode != DEFAULT_JOURNAL_MODE:
            raise ValueError("Unsupported SQLite journal mode.")
        if self.busy_timeout_ms <= 0:
            raise ValueError("SQLite busy timeout must be positive.")
        if self.connection_timeout_seconds <= 0:
            raise ValueError("SQLite connection timeout must be positive.")


@dataclass(frozen=True)
class SQLiteValidation:
    quick_check: str
    foreign_key_issues: int
    migration_versions: tuple[int, ...]

    @property
    def is_valid(self) -> bool:
        return self.quick_check == "ok" and self.foreign_key_issues == 0


@dataclass(frozen=True)
class SQLiteBackupReport:
    source: Path
    destination: Path
    source_sha256_before: str
    source_sha256_after: str
    backup_sha256: str
    validation: SQLiteValidation

    @property
    def source_unchanged(self) -> bool:
        return self.source_sha256_before == self.source_sha256_after


def sqlite_url(database_path: str | Path) -> str:
    path = Path(database_path).expanduser().resolve()
    return f"sqlite:///{path.as_posix()}"


def create_sqlite_engine(
    database_url: str,
    policy: SQLitePolicy = SQLitePolicy(),
) -> Engine:
    engine = create_engine(
        database_url,
        connect_args={"timeout": policy.connection_timeout_seconds},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={policy.busy_timeout_ms}")
            cursor.execute(f"PRAGMA journal_mode={policy.journal_mode}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


def validate_sqlite_database(database_path: str | Path) -> SQLiteValidation:
    path = _existing_file(database_path)
    with _read_only_connection(path) as connection:
        quick_rows = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        foreign_key_issues = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
        migration_versions = _migration_versions(connection)
    return SQLiteValidation("|".join(map(str, quick_rows)), foreign_key_issues, migration_versions)


def create_sqlite_backup(
    source_path: str | Path,
    destination_path: str | Path,
) -> SQLiteBackupReport:
    """Create a consistent snapshot, including committed WAL content."""
    source = _existing_file(source_path)
    destination = Path(destination_path).expanduser().resolve()
    if source == destination or source.parent == destination.parent:
        raise SQLiteOperationError("Backup destination must be outside the active DB directory.")
    if destination.exists():
        raise SQLiteOperationError("Backup destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_hash_before = _sha256(source)
    with _read_only_connection(source) as source_connection:
        with closing(sqlite3.connect(destination)) as backup_connection:
            source_connection.backup(backup_connection)
    source_hash_after = _sha256(source)
    validation = validate_sqlite_database(destination)
    if not validation.is_valid:
        raise SQLiteOperationError("SQLite backup failed integrity validation.")
    return SQLiteBackupReport(
        source=source,
        destination=destination,
        source_sha256_before=source_hash_before,
        source_sha256_after=source_hash_after,
        backup_sha256=_sha256(destination),
        validation=validation,
    )


def restore_sqlite_backup(
    backup_path: str | Path,
    destination_path: str | Path,
) -> SQLiteBackupReport:
    """Restore into a new destination; never overwrite an existing database."""
    return create_sqlite_backup(backup_path, destination_path)


def aggregate_table_counts(
    database_path: str | Path,
    table_names: Iterable[str],
) -> dict[str, int]:
    path = _existing_file(database_path)
    names = tuple(table_names)
    if any(not _SAFE_IDENTIFIER.fullmatch(name) for name in names):
        raise SQLiteOperationError("Unsafe aggregate table name.")
    with _read_only_connection(path) as connection:
        return {
            name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in names
        }


def _migration_versions(connection: sqlite3.Connection) -> tuple[int, ...]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return ()
    return tuple(
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    )


def _existing_file(database_path: str | Path) -> Path:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise SQLiteOperationError("SQLite database does not exist.")
    return path


@contextmanager
def _read_only_connection(path: Path):
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
