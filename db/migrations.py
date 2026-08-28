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


def _create_subscription_payments(connection: Connection) -> None:
    """Add isolated, auditable payment records without changing legacy payments."""
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS subscription_payments (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT 'yookassa',
            provider_payment_id TEXT,
            idempotency_key TEXT NOT NULL,
            product_code TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            period_days INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'creating',
            confirmation_url TEXT,
            provider_expires_at DATETIME,
            cancellation_code TEXT,
            failure_code TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            confirmed_at DATETIME,
            last_checked_at DATETIME,
            access_applied_at DATETIME,
            grant_started_at DATETIME,
            grant_ends_at DATETIME,
            CONSTRAINT ck_subscription_payments_provider
                CHECK (provider = 'yookassa'),
            CONSTRAINT ck_subscription_payments_amount_minor
                CHECK (amount_minor > 0),
            CONSTRAINT ck_subscription_payments_currency
                CHECK (length(currency) = 3 AND currency = upper(currency)),
            CONSTRAINT ck_subscription_payments_period_days
                CHECK (period_days > 0),
            CONSTRAINT ck_subscription_payments_status
                CHECK (status IN (
                    'creating', 'pending', 'waiting_for_capture', 'succeeded',
                    'canceled', 'expired', 'failed'
                )),
            CONSTRAINT ck_subscription_payments_grant_order
                CHECK (
                    grant_ends_at IS NULL OR grant_started_at IS NULL
                    OR grant_ends_at >= grant_started_at
                ),
            CONSTRAINT uq_subscription_payments_idempotency_key
                UNIQUE (idempotency_key),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_subscription_payments_provider_payment_id
        ON subscription_payments (provider, provider_payment_id)
        WHERE provider_payment_id IS NOT NULL
        """
    )
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_subscription_payments_active_user_product
        ON subscription_payments (user_id, product_code)
        WHERE status IN ('creating', 'pending', 'waiting_for_capture')
        """
    )
    connection.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_subscription_payments_user_created_at
        ON subscription_payments (user_id, created_at)
        """
    )


def _add_exercise_taxonomy(connection: Connection) -> None:
    """Add replacement-ready taxonomy while preserving stable exercise rows."""
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN "
        "secondary_muscle_groups TEXT NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN "
        "training_environments TEXT NOT NULL DEFAULT 'gym'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN experience_levels TEXT NOT NULL "
        "DEFAULT 'beginner,intermediate,advanced'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN "
        "movement_pattern TEXT NOT NULL DEFAULT 'isolation'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN progression_type TEXT NOT NULL "
        "DEFAULT 'external_load_reps'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE exercises ADD COLUMN equivalence_group TEXT"
    )

    legacy_taxonomy = {
        "leg_press": (
            "quads", "glutes", "gym", "beginner,intermediate,advanced",
            "squat", "external_load_reps", "knee_dominant_press",
        ),
        "seated_leg_curl": (
            "hamstrings", "", "gym", "beginner,intermediate,advanced",
            "isolation", "external_load_reps", "leg_curl",
        ),
        "chest_press": (
            "chest", "triceps,shoulders", "gym",
            "beginner,intermediate,advanced", "horizontal_push",
            "external_load_reps", "horizontal_chest_press",
        ),
        "lat_pulldown": (
            "back", "biceps", "gym,functional_gym",
            "beginner,intermediate,advanced", "vertical_pull",
            "external_load_reps", "vertical_pull",
        ),
        "seated_row": (
            "back", "biceps", "gym,functional_gym",
            "beginner,intermediate,advanced", "horizontal_pull",
            "external_load_reps", "horizontal_row",
        ),
        "shoulder_press": (
            "shoulders", "triceps", "gym",
            "beginner,intermediate,advanced", "vertical_push",
            "external_load_reps", "vertical_press",
        ),
        "cable_curl": (
            "biceps", "", "gym,functional_gym",
            "beginner,intermediate,advanced", "isolation",
            "external_load_reps", "elbow_flexion",
        ),
        "triceps_pushdown": (
            "triceps", "", "gym,functional_gym",
            "beginner,intermediate,advanced", "isolation",
            "external_load_reps", "elbow_extension",
        ),
        "hip_abduction": (
            "glutes", "", "gym", "beginner,intermediate,advanced",
            "isolation", "external_load_reps", "hip_abduction",
        ),
        "calf_raise": (
            "calves", "", "gym", "beginner,intermediate,advanced",
            "isolation", "external_load_reps", "calf_raise",
        ),
        "back_extension": (
            "glutes", "hamstrings,back", "gym,functional_gym",
            "beginner,intermediate,advanced", "hinge",
            "bodyweight_reps", "hip_hinge_extension",
        ),
        "cable_crunch": (
            "core", "", "gym,functional_gym",
            "beginner,intermediate,advanced", "core",
            "external_load_reps", "trunk_flexion",
        ),
        "barbell_bench_press": (
            "chest", "triceps,shoulders", "gym,functional_gym",
            "intermediate,advanced", "horizontal_push",
            "external_load_reps", "horizontal_chest_press",
        ),
        "dumbbell_bench_press": (
            "chest", "triceps,shoulders", "gym,functional_gym",
            "beginner,intermediate,advanced", "horizontal_push",
            "external_load_reps", "horizontal_chest_press",
        ),
        "barbell_back_squat": (
            "quads", "glutes,hamstrings,core", "gym,functional_gym",
            "intermediate,advanced", "squat", "external_load_reps",
            "barbell_squat",
        ),
    }
    for code, values in legacy_taxonomy.items():
        connection.exec_driver_sql(
            """UPDATE exercises SET
                muscle_group = ?, secondary_muscle_groups = ?,
                training_environments = ?, experience_levels = ?,
                movement_pattern = ?, progression_type = ?,
                equivalence_group = ? WHERE code = ?""",
            (*values, code),
        )


def _expand_training_profile(connection: Connection) -> None:
    """Canonicalize profile choices and leave legacy environment unknown."""
    connection.exec_driver_sql(
        """CREATE TABLE fitness_profiles_v8 (
            user_id INTEGER NOT NULL PRIMARY KEY,
            age INTEGER NOT NULL,
            sex TEXT NOT NULL,
            height_cm INTEGER NOT NULL,
            weight_kg FLOAT NOT NULL,
            goal TEXT NOT NULL,
            experience_level TEXT NOT NULL,
            training_environment TEXT,
            workouts_per_week INTEGER NOT NULL,
            session_duration_minutes INTEGER NOT NULL,
            limitations TEXT,
            completed_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_fitness_profiles_goal
                CHECK (goal IN ('muscle_gain', 'strength', 'fat_loss')),
            CONSTRAINT ck_fitness_profiles_experience_level
                CHECK (experience_level IN (
                    'beginner', 'intermediate', 'advanced'
                )),
            CONSTRAINT ck_fitness_profiles_training_environment CHECK (
                training_environment IS NULL OR training_environment IN (
                    'gym', 'functional_gym', 'street', 'home'
                )
            ),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )"""
    )
    connection.exec_driver_sql(
        """INSERT INTO fitness_profiles_v8 (
            user_id, age, sex, height_cm, weight_kg, goal,
            experience_level, training_environment, workouts_per_week,
            session_duration_minutes, limitations, completed_at, updated_at
        ) SELECT
            user_id, age, sex, height_cm, weight_kg, goal,
            CASE experience_level
                WHEN 'some_experience' THEN 'intermediate'
                WHEN 'experienced' THEN 'advanced'
                ELSE experience_level
            END,
            NULL,
            workouts_per_week, session_duration_minutes, limitations,
            completed_at, updated_at
        FROM fitness_profiles"""
    )
    connection.exec_driver_sql("DROP TABLE fitness_profiles")
    connection.exec_driver_sql(
        "ALTER TABLE fitness_profiles_v8 RENAME TO fitness_profiles"
    )


def _add_progression_strategies(connection: Connection) -> None:
    """Persist plan intent and immutable planned/selected execution strategy."""
    allowed = (
        "'hypertrophy_load_reps', 'strength_load_reps', 'bodyweight_reps'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE user_workout_plan_exercises ADD COLUMN "
        "progression_strategy TEXT CHECK (progression_strategy IS NULL OR "
        f"progression_strategy IN ({allowed}))"
    )
    connection.exec_driver_sql(
        "ALTER TABLE workout_session_exercises ADD COLUMN "
        "planned_progression_strategy TEXT CHECK ("
        "planned_progression_strategy IS NULL OR "
        f"planned_progression_strategy IN ({allowed}))"
    )
    connection.exec_driver_sql(
        "ALTER TABLE workout_session_exercises ADD COLUMN "
        "selected_progression_strategy TEXT CHECK ("
        "selected_progression_strategy IS NULL OR "
        f"selected_progression_strategy IN ({allowed}))"
    )


def _add_workout_formats(connection: Connection) -> None:
    """Add immutable plan/session blocks and durable timed-format results."""
    formats = "'standard_sets', 'amrap', 'emom', 'for_time', 'circuit_rounds'"
    connection.exec_driver_sql(
        f"""CREATE TABLE user_workout_plan_blocks (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            plan_day_id INTEGER NOT NULL,
            block_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            workout_format TEXT NOT NULL,
            duration_seconds INTEGER,
            target_rounds INTEGER,
            CONSTRAINT uq_plan_blocks_order UNIQUE (plan_day_id, block_order),
            CONSTRAINT ck_plan_blocks_order CHECK (block_order >= 1),
            CONSTRAINT ck_plan_blocks_format CHECK (workout_format IN ({formats})),
            CONSTRAINT ck_plan_blocks_duration CHECK (
                duration_seconds IS NULL OR duration_seconds > 0
            ),
            CONSTRAINT ck_plan_blocks_rounds CHECK (
                target_rounds IS NULL OR target_rounds > 0
            ),
            FOREIGN KEY(plan_day_id) REFERENCES user_workout_plan_days(id)
                ON DELETE CASCADE
        )"""
    )
    connection.exec_driver_sql(
        "ALTER TABLE user_workout_plan_exercises ADD COLUMN plan_block_id INTEGER "
        "REFERENCES user_workout_plan_blocks(id) ON DELETE SET NULL"
    )
    connection.exec_driver_sql(
        "ALTER TABLE user_workout_plan_exercises ADD COLUMN format_reps INTEGER "
        "CHECK (format_reps IS NULL OR format_reps >= 1)"
    )
    connection.exec_driver_sql(
        "ALTER TABLE user_workout_plan_exercises ADD COLUMN station_order INTEGER "
        "CHECK (station_order IS NULL OR station_order >= 1)"
    )
    connection.exec_driver_sql(
        f"""CREATE TABLE workout_session_blocks (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            source_plan_block_id INTEGER,
            block_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            workout_format TEXT NOT NULL,
            duration_seconds INTEGER,
            target_rounds INTEGER,
            started_at DATETIME,
            finished_at DATETIME,
            completed_rounds INTEGER NOT NULL DEFAULT 0,
            partial_station_order INTEGER,
            partial_reps INTEGER NOT NULL DEFAULT 0,
            completed_minutes INTEGER NOT NULL DEFAULT 0,
            missed_minutes INTEGER NOT NULL DEFAULT 0,
            elapsed_seconds INTEGER,
            final_score INTEGER,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_session_blocks_order UNIQUE (session_id, block_order),
            CONSTRAINT ck_session_blocks_order CHECK (block_order >= 1),
            CONSTRAINT ck_session_blocks_format CHECK (workout_format IN ({formats})),
            CONSTRAINT ck_session_blocks_duration CHECK (
                duration_seconds IS NULL OR duration_seconds > 0
            ),
            CONSTRAINT ck_session_blocks_rounds CHECK (
                target_rounds IS NULL OR target_rounds > 0
            ),
            CONSTRAINT ck_session_blocks_completed_rounds CHECK (completed_rounds >= 0),
            CONSTRAINT ck_session_blocks_partial_reps CHECK (partial_reps >= 0),
            CONSTRAINT ck_session_blocks_completed_minutes CHECK (completed_minutes >= 0),
            CONSTRAINT ck_session_blocks_missed_minutes CHECK (missed_minutes >= 0),
            CONSTRAINT ck_session_blocks_elapsed CHECK (
                elapsed_seconds IS NULL OR elapsed_seconds >= 0
            ),
            CONSTRAINT ck_session_blocks_score CHECK (final_score IS NULL OR final_score >= 0),
            FOREIGN KEY(session_id) REFERENCES workout_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY(source_plan_block_id) REFERENCES user_workout_plan_blocks(id)
                ON DELETE SET NULL
        )"""
    )
    connection.exec_driver_sql(
        "ALTER TABLE workout_session_exercises ADD COLUMN session_block_id INTEGER "
        "REFERENCES workout_session_blocks(id) ON DELETE SET NULL"
    )
    for name in (
        "planned_format_reps", "selected_format_reps",
        "planned_station_order", "selected_station_order",
    ):
        connection.exec_driver_sql(
            f"ALTER TABLE workout_session_exercises ADD COLUMN {name} INTEGER "
            f"CHECK ({name} IS NULL OR {name} >= 1)"
        )
    connection.exec_driver_sql(
        """CREATE TABLE workout_format_interval_results (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            session_block_id INTEGER NOT NULL,
            minute_number INTEGER NOT NULL,
            station_order INTEGER NOT NULL,
            completed INTEGER NOT NULL,
            recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_format_interval_minute
                UNIQUE (session_block_id, minute_number),
            CONSTRAINT ck_format_interval_minute CHECK (minute_number >= 1),
            CONSTRAINT ck_format_interval_station CHECK (station_order >= 1),
            CONSTRAINT ck_format_interval_completed CHECK (completed IN (0, 1)),
            FOREIGN KEY(session_block_id) REFERENCES workout_session_blocks(id)
                ON DELETE CASCADE
        )"""
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_session_blocks_session_order "
        "ON workout_session_blocks(session_id, block_order)"
    )


def _add_user_program_metadata(connection: Connection) -> None:
    """Distinguish generated/user plans and snapshot their adaptation policy."""
    for table in ("user_workout_plans", "workout_sessions"):
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN plan_source TEXT NOT NULL "
            "DEFAULT 'generated' CHECK (plan_source IN ('generated', 'user_defined'))"
        )
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN adaptation_mode TEXT NOT NULL "
            "DEFAULT 'adaptive' CHECK (adaptation_mode IN "
            "('strict', 'replacements', 'adaptive'))"
        )


def _add_effective_training_environment(connection: Connection) -> None:
    """Persist the environment selected for one workout without inventing legacy data."""
    connection.exec_driver_sql(
        "ALTER TABLE workout_sessions "
        "ADD COLUMN effective_training_environment TEXT "
        "CHECK (effective_training_environment IS NULL OR "
        "effective_training_environment IN "
        "('gym', 'functional_gym', 'street', 'home'))"
    )


MIGRATIONS = (
    Migration(1, "baseline_existing_schema", _create_baseline_schema),
    Migration(2, "fitness_profile_and_access", _create_fitness_foundation),
    Migration(3, "workout_planning", _create_workout_planning),
    Migration(4, "stage_two_architecture", _upgrade_stage_two_architecture),
    Migration(5, "workout_execution", _create_workout_execution),
    Migration(6, "subscription_payments", _create_subscription_payments),
    Migration(7, "exercise_taxonomy", _add_exercise_taxonomy),
    Migration(8, "training_profile_expansion", _expand_training_profile),
    Migration(9, "workout_progression_strategies", _add_progression_strategies),
    Migration(10, "workout_formats", _add_workout_formats),
    Migration(11, "user_program_metadata", _add_user_program_metadata),
    Migration(
        12,
        "workout_session_effective_training_environment",
        _add_effective_training_environment,
    ),
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
