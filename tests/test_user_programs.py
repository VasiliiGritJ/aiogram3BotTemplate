import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from db.migrations import run_migrations
from db.models import (
    Exercise,
    FitnessProfile,
    SqliteSession,
    User,
    UserAccess,
    UserWorkoutPlan,
    UserWorkoutPlanExercise,
)
from services.workout_execution import get_or_start_workout
from services.user_programs import (
    AdaptationMode,
    DeterministicRussianProgramParser,
    ParseIssueCode,
    UserProgramDraft,
    assign_user_program,
    format_user_program_preview,
)
from services.workout_plans import ensure_workout_catalog


PROGRAM = """День 1 — грудь/трицепс
1. Жим лежа — 4 по 8
2. Наклонные гантели — 3x10-12
Трицепс на блоке 3 подхода по 12

Среда
Жим гантелей под углом 3×10
Тяга верхнего блока 3x10"""


class UserProgramParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = DeterministicRussianProgramParser()

    def test_multiday_aliases_and_prescription_variants(self):
        result = self.parser.parse(PROGRAM, AdaptationMode.ADAPTIVE, training_environment="gym")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(2, len(result.draft.days))
        first = result.draft.days[0].exercises
        self.assertEqual(("barbell_bench_press", 4, 8, 8), (
            first[0].exercise_code, first[0].sets, first[0].reps_min, first[0].reps_max
        ))
        self.assertEqual((10, 12), (first[1].reps_min, first[1].reps_max))
        self.assertIn("Я понял программу так", format_user_program_preview(result.draft))

    def test_weekday_variants_and_single_implicit_day(self):
        for heading in ("Пн", "Понедельник", "День 1"):
            with self.subTest(heading=heading):
                result = self.parser.parse(
                    f"{heading}\nЖим штанги лежа 3x10",
                    AdaptationMode.STRICT,
                    training_environment="gym",
                )
                self.assertTrue(result.valid, result.issues)
        implicit = self.parser.parse("Жим штанги лежа 3×10", AdaptationMode.STRICT)
        self.assertTrue(implicit.valid)

    def test_unresolved_ambiguous_malformed_and_bounds_fail_closed(self):
        cases = (
            ("День 1\nНеизвестный дракон 3x10", ParseIssueCode.UNRESOLVED_EXERCISE),
            ("День 1\nЖим 3x10", ParseIssueCode.AMBIGUOUS_EXERCISE),
            ("День 1\nЖим лежа без подходов", ParseIssueCode.MALFORMED_PRESCRIPTION),
            ("День 1\nЖим лежа 50x500", ParseIssueCode.MALFORMED_PRESCRIPTION),
            ("AMRAP 10 минут\nПриседания 3x10", ParseIssueCode.UNSUPPORTED_FORMAT),
        )
        for text, reason in cases:
            with self.subTest(reason=reason):
                result = self.parser.parse(text, AdaptationMode.STRICT)
                self.assertFalse(result.valid)
                self.assertIn(reason, {issue.code for issue in result.issues})
        long_result = self.parser.parse("x" * 6001, AdaptationMode.STRICT)
        self.assertEqual(ParseIssueCode.INPUT_TOO_LONG, long_result.issues[0].code)

    def test_environment_duplicate_and_empty_day_validation(self):
        mismatch = self.parser.parse(
            "День 1\nЖим штанги лежа 3x10", AdaptationMode.STRICT,
            training_environment="street",
        )
        self.assertEqual(ParseIssueCode.ENVIRONMENT_MISMATCH, mismatch.issues[0].code)
        duplicate = self.parser.parse(
            "День 1\nЖим лежа 3x10\nЖим штанги лежа 3x8",
            AdaptationMode.STRICT,
        )
        self.assertIn(ParseIssueCode.DUPLICATE_EXERCISE, {item.code for item in duplicate.issues})
        empty = self.parser.parse("День 1", AdaptationMode.STRICT)
        self.assertIn(ParseIssueCode.EMPTY_DAY, {item.code for item in empty.issues})


class UserProgramPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "user-program.db"
        self.db = SqliteSession(f"sqlite:///{path.as_posix()}")
        run_migrations(self.db.engine)
        ensure_workout_catalog(self.db)
        with self.db() as session:
            user = User(tg_id=8800, fullname="Program", username="program", inviter_id=0)
            session.add(user)
            session.flush()
            self.user_id = user.id
            session.add(FitnessProfile(
                user_id=user.id, age=30, sex="male", height_cm=180, weight_kg=80,
                goal="muscle_gain", experience_level="intermediate",
                training_environment="gym", workouts_per_week=3,
                session_duration_minutes=60, limitations="none",
                completed_at=datetime(2026, 8, 13), updated_at=datetime(2026, 8, 13),
            ))
            session.add(UserAccess(user_id=user.id, updated_at=datetime(2026, 8, 13)))
            session.commit()
        self.parser = DeterministicRussianProgramParser()

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()

    def draft(self, mode):
        result = self.parser.parse(
            "День 1\nЖим лежа 4x8\nТяга верхнего блока 3x10-12",
            mode,
            training_environment="gym",
        )
        self.assertTrue(result.valid, result.issues)
        return result.draft

    def test_parse_does_not_save_and_confirm_is_idempotent(self):
        draft = self.draft(AdaptationMode.ADAPTIVE)
        with self.db() as session:
            self.assertIsNone(session.query(UserWorkoutPlan).filter_by(user_id=self.user_id).one_or_none())
        first = assign_user_program(self.user_id, draft, self.db)
        second = assign_user_program(self.user_id, UserProgramDraft.from_payload(draft.to_payload()), self.db)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        with self.db() as session:
            plan = session.query(UserWorkoutPlan).filter_by(user_id=self.user_id).one()
            self.assertEqual("user_defined", plan.plan_source)
            self.assertEqual("adaptive", plan.adaptation_mode)
            self.assertEqual(2, session.query(UserWorkoutPlanExercise).count())
            stored_codes = {
                exercise.code
                for exercise in session.query(Exercise)
                .join(UserWorkoutPlanExercise, UserWorkoutPlanExercise.exercise_id == Exercise.id)
                .all()
            }
            self.assertEqual({"barbell_bench_press", "lat_pulldown"}, stored_codes)

    def test_modes_persist_and_route_progression_conservatively(self):
        for mode, expected in (
            (AdaptationMode.STRICT, {None}),
            (AdaptationMode.REPLACEMENTS, {None}),
            (AdaptationMode.ADAPTIVE, {"hypertrophy_load_reps"}),
        ):
            with self.subTest(mode=mode):
                assign_user_program(self.user_id, self.draft(mode), self.db)
                with self.db() as session:
                    plan = session.query(UserWorkoutPlan).filter_by(user_id=self.user_id).one()
                    strategies = {row.progression_strategy for row in session.query(UserWorkoutPlanExercise).all()}
                    self.assertEqual(mode.value, plan.adaptation_mode)
                    self.assertEqual(expected, strategies)

        started = get_or_start_workout(
            self.user_id,
            datetime(2026, 8, 13, 12, 0),
            self.db,
        )
        self.assertEqual("user_defined", started.workout.plan_source)
        self.assertEqual("adaptive", started.workout.adaptation_mode)


if __name__ == "__main__":
    unittest.main()
