"""Shared fail-closed bootstrap checks for long-running application roles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from db.migrations import MIGRATIONS
from db.models import configure_database
from db.runtime import SQLiteValidation, validate_sqlite_database
from storage.payment_runtime import PaymentMode
from storage.runtime_config import ApplicationEnvironment, RuntimeConfigurationError


@dataclass(frozen=True)
class DatabaseRuntimeState:
    database_path: Path
    validation: SQLiteValidation


def initialize_database_runtime(database_path: Path) -> DatabaseRuntimeState:
    """Validate the persisted schema before configuring process dependencies."""
    try:
        validation = validate_sqlite_database(database_path)
    except Exception:
        raise RuntimeConfigurationError("Database runtime validation failed.") from None
    expected_versions = tuple(migration.version for migration in MIGRATIONS)
    if not validation.is_valid or validation.migration_versions != expected_versions:
        raise RuntimeConfigurationError("Database migrations are incomplete or invalid.")
    configure_database(str(database_path))
    return DatabaseRuntimeState(database_path, validation)


def validate_payment_environment(
    environment: ApplicationEnvironment,
    payment_mode: PaymentMode,
) -> None:
    expected_mode = (
        PaymentMode.PRODUCTION
        if environment is ApplicationEnvironment.PRODUCTION
        else PaymentMode.TEST
    )
    if payment_mode is not expected_mode:
        raise RuntimeConfigurationError(
            "Application environment and payment mode do not match."
        )
