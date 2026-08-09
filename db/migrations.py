"""Small, dependency-free schema migration runner for SQLite."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Connection, Engine


class MigrationError(RuntimeError):
    """Base error raised when a database migration cannot be applied safely."""


class DuplicateTelegramIdsError(MigrationError):
    """Raised before adding uniqueness when duplicate Telegram IDs exist."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    upgrade: Callable[[Connection], None]


def _create_baseline_schema(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS users (
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
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user INTEGER NOT NULL,
            yoo_id TEXT NOT NULL,
            link TEXT NOT NULL,
            status TEXT NOT NULL,
            reg_datetime DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user) REFERENCES users (id)
        )
        """
    )


def _create_fitness_foundation(connection: Connection) -> None:
    duplicate_count = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM (
            SELECT tg_id
            FROM users
            GROUP BY tg_id
            HAVING COUNT(*) > 1
        ) AS duplicate_tg_ids
        """
    ).scalar_one()
    if duplicate_count:
        raise DuplicateTelegramIdsError(
            "Migration stopped: users contains "
            f"{duplicate_count} duplicate tg_id value(s)."
        )

    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_tg_id ON users (tg_id)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS fitness_profiles (
            user_id INTEGER NOT NULL PRIMARY KEY,
            age INTEGER NOT NULL,
            sex TEXT NOT NULL,
            height_cm INTEGER NOT NULL,
            weight_kg FLOAT NOT NULL,
            goal TEXT NOT NULL,
            experience_level TEXT NOT NULL,
            workouts_per_week INTEGER NOT NULL,
            session_duration_minutes INTEGER NOT NULL,
            limitations TEXT,
            completed_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_fitness_profiles_goal
                CHECK (goal IN ('muscle_gain', 'fat_loss')),
            CONSTRAINT ck_fitness_profiles_experience_level
                CHECK (experience_level IN ('beginner', 'some_experience')),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_access (
            user_id INTEGER NOT NULL PRIMARY KEY,
            trial_started_at DATETIME NOT NULL,
            trial_ends_at DATETIME NOT NULL,
            subscription_started_at DATETIME,
            subscription_ends_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )


MIGRATIONS = (
    Migration(1, "baseline_existing_schema", _create_baseline_schema),
    Migration(2, "fitness_profile_and_access", _create_fitness_foundation),
)


def _ensure_migration_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def run_migrations(engine: Engine) -> tuple[int, ...]:
    """Apply pending migrations in order and return newly applied versions."""
    with engine.begin() as connection:
        _ensure_migration_table(connection)

    applied_now: list[int] = []
    for migration in MIGRATIONS:
        with engine.begin() as connection:
            already_applied = connection.exec_driver_sql(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (migration.version,),
            ).first()
            if already_applied:
                continue

            migration.upgrade(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            applied_now.append(migration.version)

    return tuple(applied_now)

