import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db.migrations import run_migrations
from db.models import FitnessProfile, SqliteSession, User, UserAccess
from services.onboarding import (
    EXPERIENCE_LABELS,
    GOAL_LABELS,
    OnboardingData,
    OnboardingValidationError,
    normalize_limitations,
    parse_age,
    parse_height_cm,
    parse_session_duration_minutes,
    parse_weight_kg,
    parse_workouts_per_week,
    save_profile_and_trial,
    update_existing_profile,
    validate_choice,
)
from services.workout_plans import LIMITATIONS_NOTICE, assign_workout_plan


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class OnboardingValidationTests(unittest.TestCase):
    def assert_invalid(self, parser, *values: str) -> None:
        for value in values:
            with self.subTest(parser=parser.__name__, value=value):
                with self.assertRaises(OnboardingValidationError):
                    parser(value)

    def test_age_validation(self) -> None:
        self.assertEqual(18, parse_age("18"))
        self.assertEqual(120, parse_age("120"))
        self.assert_invalid(parse_age, "17", "121", "18.5", "abc", "")

    def test_height_validation(self) -> None:
        self.assertEqual(100, parse_height_cm("100"))
        self.assertEqual(250, parse_height_cm("250"))
        self.assert_invalid(parse_height_cm, "99", "251", "175.5", "abc", "")

    def test_weight_validation(self) -> None:
        self.assertEqual(72.5, parse_weight_kg("72,5"))
        self.assertEqual(80.25, parse_weight_kg("80.25"))
        self.assert_invalid(
            parse_weight_kg,
            "29.9",
            "500.1",
            "nan",
            "inf",
            "abc",
            "",
        )

    def test_workouts_per_week_validation(self) -> None:
        self.assertEqual(1, parse_workouts_per_week("1"))
        self.assertEqual(7, parse_workouts_per_week("7"))
        self.assert_invalid(parse_workouts_per_week, "0", "8", "2.5", "abc", "")

    def test_session_duration_validation(self) -> None:
        self.assertEqual(10, parse_session_duration_minutes("10"))
        self.assertEqual(300, parse_session_duration_minutes("300"))
        self.assert_invalid(
            parse_session_duration_minutes,
            "9",
            "301",
            "60.5",
            "abc",
            "",
        )

    def test_choice_validation(self) -> None:
        self.assertEqual(
            "muscle_gain",
            validate_choice("muscle_gain", GOAL_LABELS, "цель"),
        )
        self.assertEqual(
            "beginner",
            validate_choice("beginner", EXPERIENCE_LABELS, "опыт"),
        )
        with self.assertRaises(OnboardingValidationError):
            validate_choice("unknown", GOAL_LABELS, "цель")

    def test_limitations_normalization(self) -> None:
        self.assertIsNone(normalize_limitations("нет"))
        self.assertIsNone(normalize_limitations(" Нет ограничений "))
        self.assertIsNone(normalize_limitations(" НЕТУ "))
        self.assertIsNone(normalize_limitations(" no "))
        self.assertIsNone(normalize_limitations("None"))
        self.assertEqual("Болит колено", normalize_limitations(" Болит колено "))
        self.assert_invalid(normalize_limitations, "", "x" * 501)


class OnboardingPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database = Path(self.temp_directory.name) / "onboarding.db"
        self.database = SqliteSession(sqlite_url(database))
        run_migrations(self.database.engine)
        with self.database() as session:
            user = User(
                tg_id=123456789,
                fullname="Test User",
                username="test_user",
                inviter_id=0,
            )
            session.add(user)
            session.commit()
            self.user_id = user.id

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    @staticmethod
    def valid_data(**overrides) -> OnboardingData:
        values = {
            "age": 30,
            "sex": "male",
            "height_cm": 180,
            "weight_kg": 80.5,
            "goal": "muscle_gain",
            "experience_level": "beginner",
            "workouts_per_week": 3,
            "session_duration_minutes": 60,
            "limitations": None,
        }
        values.update(overrides)
        return OnboardingData(**values)

    def test_profile_and_three_day_trial_are_created_together(self) -> None:
        confirmed_at = datetime(2026, 8, 9, 15, 30, tzinfo=timezone.utc)

        result = save_profile_and_trial(
            self.user_id,
            self.valid_data(),
            confirmed_at,
            self.database,
        )

        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            access = session.get(UserAccess, self.user_id)

        expected_start = confirmed_at.replace(tzinfo=None)
        self.assertTrue(result.created)
        self.assertIsNotNone(profile)
        self.assertIsNotNone(access)
        self.assertEqual(expected_start, profile.completed_at)
        self.assertEqual(expected_start, access.trial_started_at)
        self.assertEqual(expected_start + timedelta(days=3), access.trial_ends_at)
        self.assertIsNone(access.subscription_started_at)
        self.assertIsNone(access.subscription_ends_at)

    def test_repeated_confirmation_does_not_restart_trial(self) -> None:
        first_confirmation = datetime(2026, 8, 9, 12, 0)
        first = save_profile_and_trial(
            self.user_id,
            self.valid_data(),
            first_confirmation,
            self.database,
        )
        repeated = save_profile_and_trial(
            self.user_id,
            self.valid_data(weight_kg=90.0),
            first_confirmation + timedelta(days=1),
            self.database,
        )

        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        self.assertEqual(first.access.trial_started_at, repeated.access.trial_started_at)
        self.assertEqual(80.5, repeated.profile.weight_kg)

    def test_failed_profile_insert_rolls_back_access(self) -> None:
        invalid_data = self.valid_data(goal="unsupported_goal")

        with self.assertRaises(IntegrityError):
            save_profile_and_trial(
                self.user_id,
                invalid_data,
                datetime(2026, 8, 9, 12, 0),
                self.database,
            )

        with self.database() as session:
            self.assertIsNone(session.get(FitnessProfile, self.user_id))
            self.assertIsNone(session.get(UserAccess, self.user_id))

    def test_editing_profile_preserves_trial_and_allows_plan_after_fix(self) -> None:
        first_confirmation = datetime(2026, 8, 9, 12, 0)
        save_profile_and_trial(
            self.user_id,
            self.valid_data(limitations="ytp"),
            first_confirmation,
            self.database,
        )
        plan_with_limitations = assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            access_before = session.get(UserAccess, self.user_id)
            trial_started_before = access_before.trial_started_at
            trial_ends_before = access_before.trial_ends_at

        updated = update_existing_profile(
            self.user_id,
            self.valid_data(limitations=normalize_limitations("нету")),
            first_confirmation + timedelta(days=1),
            self.database,
        )
        plan = assign_workout_plan(self.user_id, self.database)

        with self.database() as session:
            profile_count = session.scalar(
                select(func.count(FitnessProfile.user_id))
            )
            access_count = session.scalar(select(func.count(UserAccess.user_id)))
            access_after = session.get(UserAccess, self.user_id)
            profile_after = session.get(FitnessProfile, self.user_id)

        self.assertEqual(1, profile_count)
        self.assertEqual(1, access_count)
        self.assertIsNone(updated.limitations)
        self.assertIsNone(profile_after.limitations)
        self.assertEqual(trial_started_before, access_after.trial_started_at)
        self.assertEqual(trial_ends_before, access_after.trial_ends_at)
        self.assertTrue(plan_with_limitations.created)
        self.assertIn(LIMITATIONS_NOTICE, plan_with_limitations.fallback_notes)
        self.assertTrue(plan.created)
        self.assertNotIn(LIMITATIONS_NOTICE, plan.fallback_notes)

    def test_editing_profile_keeps_actual_limitation_description(self) -> None:
        confirmed_at = datetime(2026, 8, 9, 12, 0)
        save_profile_and_trial(
            self.user_id,
            self.valid_data(),
            confirmed_at,
            self.database,
        )

        update_existing_profile(
            self.user_id,
            self.valid_data(
                limitations=normalize_limitations(" Болит колено ")
            ),
            confirmed_at + timedelta(days=1),
            self.database,
        )

        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)

        self.assertEqual("Болит колено", profile.limitations)


if __name__ == "__main__":
    unittest.main()
