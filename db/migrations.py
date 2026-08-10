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


def _create_workout_planning(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            muscle_group TEXT NOT NULL,
            equipment TEXT NOT NULL,
            hint TEXT NOT NULL,
            restriction_tags TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_templates (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            experience_level TEXT NOT NULL,
            workouts_per_week INTEGER NOT NULL,
            duration_bucket TEXT NOT NULL,
            equipment TEXT NOT NULL,
            CONSTRAINT ck_workout_templates_goal
                CHECK (goal IN ('muscle_gain', 'fat_loss')),
            CONSTRAINT ck_workout_templates_experience_level
                CHECK (experience_level IN ('beginner', 'some_experience')),
            CONSTRAINT ck_workout_templates_workouts_per_week
                CHECK (workouts_per_week BETWEEN 1 AND 4),
            CONSTRAINT ck_workout_templates_duration_bucket
                CHECK (duration_bucket IN ('short', 'standard'))
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_template_days (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            CONSTRAINT uq_workout_template_days_order
                UNIQUE (template_id, day_number),
            CONSTRAINT ck_workout_template_days_day_number
                CHECK (day_number >= 1),
            FOREIGN KEY(template_id) REFERENCES workout_templates (id)
                ON DELETE CASCADE
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_template_exercises (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            template_day_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            exercise_order INTEGER NOT NULL,
            sets INTEGER NOT NULL,
            reps_min INTEGER NOT NULL,
            reps_max INTEGER NOT NULL,
            rest_seconds INTEGER NOT NULL,
            CONSTRAINT uq_workout_template_exercises_order
                UNIQUE (template_day_id, exercise_order),
            CONSTRAINT ck_workout_template_exercises_order
                CHECK (exercise_order >= 1),
            CONSTRAINT ck_workout_template_exercises_sets CHECK (sets >= 1),
            CONSTRAINT ck_workout_template_exercises_reps
                CHECK (reps_min >= 1 AND reps_max >= reps_min),
            CONSTRAINT ck_workout_template_exercises_rest
                CHECK (rest_seconds >= 0),
            FOREIGN KEY(template_day_id) REFERENCES workout_template_days (id)
                ON DELETE CASCADE,
            FOREIGN KEY(exercise_id) REFERENCES exercises (id)
                ON DELETE RESTRICT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_workout_plans (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            template_id INTEGER NOT NULL,
            profile_signature TEXT NOT NULL,
            assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(template_id) REFERENCES workout_templates (id)
                ON DELETE RESTRICT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_workout_plan_days (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            CONSTRAINT uq_user_workout_plan_days_order
                UNIQUE (plan_id, day_number),
            CONSTRAINT ck_user_workout_plan_days_day_number
                CHECK (day_number >= 1),
            FOREIGN KEY(plan_id) REFERENCES user_workout_plans (id)
                ON DELETE CASCADE
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS user_workout_plan_exercises (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            plan_day_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            exercise_order INTEGER NOT NULL,
            exercise_name TEXT NOT NULL,
            sets INTEGER NOT NULL,
            reps_min INTEGER NOT NULL,
            reps_max INTEGER NOT NULL,
            rest_seconds INTEGER NOT NULL,
            hint TEXT NOT NULL,
            CONSTRAINT uq_user_workout_plan_exercises_order
                UNIQUE (plan_day_id, exercise_order),
            CONSTRAINT ck_user_workout_plan_exercises_order
                CHECK (exercise_order >= 1),
            CONSTRAINT ck_user_workout_plan_exercises_sets CHECK (sets >= 1),
            CONSTRAINT ck_user_workout_plan_exercises_reps
                CHECK (reps_min >= 1 AND reps_max >= reps_min),
            CONSTRAINT ck_user_workout_plan_exercises_rest
                CHECK (rest_seconds >= 0),
            FOREIGN KEY(plan_day_id) REFERENCES user_workout_plan_days (id)
                ON DELETE CASCADE,
            FOREIGN KEY(exercise_id) REFERENCES exercises (id)
                ON DELETE RESTRICT
        )
        """
    )


def _upgrade_stage_two_architecture(connection: Connection) -> None:
    """Add structured exercise data and a not-yet-started trial state."""
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN primary_muscle_group TEXT NOT NULL DEFAULT 'other'"
    )
    connection.exec_driver_sql("ALTER TABLE exercises ADD COLUMN variant TEXT")
    connection.exec_driver_sql("ALTER TABLE exercises ADD COLUMN alternative_name TEXT")
    connection.exec_driver_sql(
        "ALTER TABLE user_workout_plan_exercises ADD COLUMN primary_muscle_group TEXT NOT NULL DEFAULT 'other'"
    )
    connection.exec_driver_sql(
        """CREATE TABLE fitness_profiles_v4 (
            user_id INTEGER NOT NULL PRIMARY KEY, age INTEGER NOT NULL,
            sex TEXT NOT NULL, height_cm INTEGER NOT NULL, weight_kg FLOAT NOT NULL,
            goal TEXT NOT NULL, experience_level TEXT NOT NULL,
            workouts_per_week INTEGER NOT NULL, session_duration_minutes INTEGER NOT NULL,
            limitations TEXT, completed_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_fitness_profiles_goal CHECK (goal IN ('muscle_gain', 'fat_loss')),
            CONSTRAINT ck_fitness_profiles_experience_level
                CHECK (experience_level IN ('beginner', 'some_experience', 'experienced')),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )"""
    )
    connection.exec_driver_sql(
        """INSERT INTO fitness_profiles_v4
        SELECT user_id, age, sex, height_cm, weight_kg, goal, experience_level,
               workouts_per_week, session_duration_minutes, limitations,
               completed_at, updated_at FROM fitness_profiles"""
    )
    connection.exec_driver_sql("DROP TABLE fitness_profiles")
    connection.exec_driver_sql("ALTER TABLE fitness_profiles_v4 RENAME TO fitness_profiles")
    connection.exec_driver_sql(
        """CREATE TABLE user_access_v4 (
            user_id INTEGER NOT NULL PRIMARY KEY, trial_started_at DATETIME,
            trial_ends_at DATETIME, subscription_started_at DATETIME,
            subscription_ends_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )"""
    )
    connection.exec_driver_sql(
        """INSERT INTO user_access_v4
        SELECT user_id, trial_started_at, trial_ends_at, subscription_started_at,
               subscription_ends_at, updated_at FROM user_access"""
    )
    connection.exec_driver_sql("DROP TABLE user_access")
    connection.exec_driver_sql("ALTER TABLE user_access_v4 RENAME TO user_access")


def _create_workout_execution(connection: Connection) -> None:
    """Add durable workout sessions, exercise snapshots, and set results."""
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_plan_id INTEGER,
            source_plan_day_id INTEGER,
            day_number INTEGER NOT NULL,
            day_title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_workout_sessions_status
                CHECK (status IN ('in_progress', 'completed', 'cancelled')),
            CONSTRAINT ck_workout_sessions_day_number
                CHECK (day_number >= 1),
            CONSTRAINT ck_workout_sessions_finished_at CHECK (
                (status = 'in_progress' AND finished_at IS NULL)
                OR (status IN ('completed', 'cancelled') AND finished_at IS NOT NULL)
            ),
            CONSTRAINT ck_workout_sessions_time_order
                CHECK (finished_at IS NULL OR finished_at >= started_at),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(source_plan_id) REFERENCES user_workout_plans (id)
                ON DELETE SET NULL,
            FOREIGN KEY(source_plan_day_id) REFERENCES user_workout_plan_days (id)
                ON DELETE SET NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_sessions_active_user
        ON workout_sessions (user_id)
        WHERE status = 'in_progress'
        """
    )
    connection.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_workout_sessions_user_finished_at
        ON workout_sessions (user_id, finished_at)
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_session_exercises (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            source_plan_exercise_id INTEGER,
            exercise_order INTEGER NOT NULL,
            planned_exercise_id INTEGER,
            planned_exercise_name TEXT NOT NULL,
            planned_primary_muscle_group TEXT NOT NULL,
            planned_target_sets INTEGER NOT NULL,
            planned_target_reps_min INTEGER NOT NULL,
            planned_target_reps_max INTEGER NOT NULL,
            planned_rest_seconds INTEGER NOT NULL,
            planned_hint TEXT NOT NULL,
            selected_exercise_id INTEGER,
            selected_exercise_name TEXT NOT NULL,
            selected_primary_muscle_group TEXT NOT NULL,
            selected_target_sets INTEGER NOT NULL,
            selected_target_reps_min INTEGER NOT NULL,
            selected_target_reps_max INTEGER NOT NULL,
            selected_rest_seconds INTEGER NOT NULL,
            selected_hint TEXT NOT NULL,
            CONSTRAINT uq_workout_session_exercises_order
                UNIQUE (session_id, exercise_order),
            CONSTRAINT ck_workout_session_exercises_order
                CHECK (exercise_order >= 1),
            CONSTRAINT ck_workout_session_exercises_planned_sets
                CHECK (planned_target_sets >= 1),
            CONSTRAINT ck_workout_session_exercises_planned_reps CHECK (
                planned_target_reps_min >= 1
                AND planned_target_reps_max >= planned_target_reps_min
            ),
            CONSTRAINT ck_workout_session_exercises_planned_rest
                CHECK (planned_rest_seconds >= 0),
            CONSTRAINT ck_workout_session_exercises_selected_sets
                CHECK (selected_target_sets >= 1),
            CONSTRAINT ck_workout_session_exercises_selected_reps CHECK (
                selected_target_reps_min >= 1
                AND selected_target_reps_max >= selected_target_reps_min
            ),
            CONSTRAINT ck_workout_session_exercises_selected_rest
                CHECK (selected_rest_seconds >= 0),
            FOREIGN KEY(session_id) REFERENCES workout_sessions (id)
                ON DELETE CASCADE,
            FOREIGN KEY(source_plan_exercise_id)
                REFERENCES user_workout_plan_exercises (id) ON DELETE SET NULL,
            FOREIGN KEY(planned_exercise_id) REFERENCES exercises (id)
                ON DELETE SET NULL,
            FOREIGN KEY(selected_exercise_id) REFERENCES exercises (id)
                ON DELETE SET NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS workout_set_results (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            session_exercise_id INTEGER NOT NULL,
            set_number INTEGER NOT NULL,
            actual_weight_kg FLOAT NOT NULL,
            actual_reps INTEGER NOT NULL,
            completed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_workout_set_results_set_number
                UNIQUE (session_exercise_id, set_number),
            CONSTRAINT ck_workout_set_results_set_number
                CHECK (set_number >= 1),
            CONSTRAINT ck_workout_set_results_weight
                CHECK (actual_weight_kg >= 0),
            CONSTRAINT ck_workout_set_results_reps
                CHECK (actual_reps >= 1),
            FOREIGN KEY(session_exercise_id)
                REFERENCES workout_session_exercises (id) ON DELETE CASCADE
        )
        """
    )


MIGRATIONS = (
    Migration(1, "baseline_existing_schema", _create_baseline_schema),
    Migration(2, "fitness_profile_and_access", _create_fitness_foundation),
    Migration(3, "workout_planning", _create_workout_planning),
    Migration(4, "stage_two_architecture", _upgrade_stage_two_architecture),
    Migration(5, "workout_execution", _create_workout_execution),
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


def run_migrations(
    engine: Engine,
    target_version: int | None = None,
) -> tuple[int, ...]:
    """Apply pending migrations in order and return newly applied versions."""
    with engine.begin() as connection:
        _ensure_migration_table(connection)

    applied_now: list[int] = []
    for migration in MIGRATIONS:
        if target_version is not None and migration.version > target_version:
            break
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
