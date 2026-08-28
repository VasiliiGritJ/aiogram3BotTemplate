import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from db.migrations import run_migrations
from db.models import (
    Exercise, FitnessProfile, User, UserAccess, UserWorkoutPlan, UserWorkoutPlanBlock,
    UserWorkoutPlanDay, UserWorkoutPlanExercise, WorkoutSession,
    WorkoutSessionBlock, WorkoutSessionExercise, WorkoutTemplate, SqliteSession,
)
from services.workout_execution import get_active_workout, get_current_step, get_or_start_workout
from services.workout_formats import (
    WorkoutFormat,
    finish_format_block,
    get_format_state,
    get_format_progression,
    record_completed_round,
    record_emom_minute,
    start_format_block,
)


BASE = datetime(2026, 8, 13, 12, 0)


class WorkoutFormatExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "formats.db"
        self.db = SqliteSession(f"sqlite:///{path.as_posix()}")
        run_migrations(self.db.engine)
        with self.db() as session:
            user = User(tg_id=777, fullname="Format", username="format", inviter_id=0)
            session.add(user)
            session.flush()
            self.user_id = user.id
            session.add(FitnessProfile(
                user_id=user.id, age=30, sex="male", height_cm=180,
                weight_kg=80, goal="fat_loss", experience_level="beginner",
                training_environment="functional_gym", workouts_per_week=2,
                session_duration_minutes=30, limitations=None,
                completed_at=BASE, updated_at=BASE,
            ))
            session.commit()

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()

    def make_block(self, workout_format, duration=300, rounds=None):
        with self.db() as session:
            workout = WorkoutSession(
                user_id=self.user_id, day_number=1, day_title="Functional",
                status="in_progress", started_at=BASE, updated_at=BASE,
            )
            session.add(workout)
            session.flush()
            block = WorkoutSessionBlock(
                session_id=workout.id, block_order=1, title="Функциональный тренинг",
                workout_format=workout_format, duration_seconds=duration,
                target_rounds=rounds, updated_at=BASE,
            )
            session.add(block)
            session.flush()
            for order, (name, reps) in enumerate((("Приседания", 8), ("Отжимания", 6)), 1):
                session.add(WorkoutSessionExercise(
                    session_id=workout.id, session_block_id=block.id,
                    exercise_order=order, planned_exercise_name=name,
                    planned_primary_muscle_group="test", planned_target_sets=1,
                    planned_target_reps_min=reps, planned_target_reps_max=reps,
                    planned_rest_seconds=0, planned_hint="", planned_format_reps=reps,
                    planned_station_order=order, selected_exercise_name=name,
                    selected_primary_muscle_group="test", selected_target_sets=1,
                    selected_target_reps_min=reps, selected_target_reps_max=reps,
                    selected_rest_seconds=0, selected_hint="", selected_format_reps=reps,
                    selected_station_order=order,
                ))
            session.commit()
            return block.id

    def test_amrap_score_round_idempotency_and_restart(self):
        block_id = self.make_block("amrap")
        self.assertTrue(start_format_block(self.user_id, block_id, BASE, self.db).created)
        self.assertFalse(start_format_block(self.user_id, block_id, BASE, self.db).created)
        self.assertTrue(record_completed_round(self.user_id, block_id, 0, BASE, self.db).created)
        self.assertFalse(record_completed_round(self.user_id, block_id, 0, BASE, self.db).created)
        result = finish_format_block(
            self.user_id, block_id, partial_station_order=1, partial_reps=3,
            now=BASE + timedelta(minutes=5), session_factory=self.db,
        )
        self.assertEqual(17, result.state.final_score)
        self.assertEqual(300, result.state.elapsed_seconds)
        self.assertFalse(finish_format_block(self.user_id, block_id, now=BASE + timedelta(minutes=6), session_factory=self.db).created)
        self.assertEqual(17, get_format_state(self.user_id, block_id, session_factory=self.db).final_score)

    def test_emom_derives_minute_and_records_complete_or_miss_once(self):
        block_id = self.make_block("emom", duration=240)
        start_format_block(self.user_id, block_id, BASE, self.db)
        state = get_format_state(self.user_id, block_id, BASE + timedelta(seconds=61), self.db)
        self.assertEqual(2, state.current_minute)
        self.assertTrue(record_emom_minute(self.user_id, block_id, 1, True, BASE, self.db).created)
        self.assertFalse(record_emom_minute(self.user_id, block_id, 1, True, BASE, self.db).created)
        state = record_emom_minute(self.user_id, block_id, 2, False, BASE, self.db).state
        self.assertEqual((1, 1), (state.completed_minutes, state.missed_minutes))

    def test_for_time_and_circuit_results_are_structured(self):
        for workout_format in (WorkoutFormat.FOR_TIME, WorkoutFormat.CIRCUIT_ROUNDS):
            with self.subTest(workout_format=workout_format):
                block_id = self.make_block(workout_format, duration=None, rounds=2)
                start_format_block(self.user_id, block_id, BASE, self.db)
                record_completed_round(self.user_id, block_id, 0, BASE, self.db)
                record_completed_round(self.user_id, block_id, 1, BASE, self.db)
                state = finish_format_block(
                    self.user_id, block_id, now=BASE + timedelta(seconds=95), session_factory=self.db
                ).state
                self.assertEqual(2, state.completed_rounds)
                self.assertEqual(95, state.elapsed_seconds)
                self.assertEqual(2, state.final_score)
                with self.db() as session:
                    workout = session.get(WorkoutSession, state.session_id)
                    workout.status = "completed"
                    workout.finished_at = BASE + timedelta(seconds=95)
                    session.commit()

    def test_history_progression_uses_same_structure_without_mutation(self):
        previous_id = self.make_block("amrap")
        start_format_block(self.user_id, previous_id, BASE, self.db)
        record_completed_round(self.user_id, previous_id, 0, BASE, self.db)
        state = finish_format_block(
            self.user_id, previous_id, now=BASE + timedelta(minutes=5),
            session_factory=self.db,
        ).state
        with self.db() as session:
            workout = session.get(WorkoutSession, state.session_id)
            workout.status = "completed"
            workout.finished_at = BASE + timedelta(minutes=5)
            session.commit()
        current_id = self.make_block("amrap")
        with self.db() as session:
            current = session.get(WorkoutSessionBlock, current_id)
            workout = session.get(WorkoutSession, current.session_id)
            workout.started_at = BASE + timedelta(days=1)
            session.commit()
        recommendation = get_format_progression(self.user_id, current_id, self.db)
        self.assertEqual("beat_score", recommendation.message_code)
        self.assertEqual(14, recommendation.previous_score)

    def test_plan_block_is_snapshotted_and_resume_uses_database(self):
        with self.db() as session:
            session.add(UserAccess(
                user_id=self.user_id, trial_started_at=BASE,
                trial_ends_at=BASE + timedelta(days=2), updated_at=BASE,
            ))
            template = WorkoutTemplate(
                code="format-template", name="Format", goal="fat_loss",
                experience_level="beginner", workouts_per_week=2,
                duration_bucket="short", equipment="functional_gym",
            )
            session.add(template)
            session.flush()
            plan = UserWorkoutPlan(
                user_id=self.user_id, template_id=template.id,
                profile_signature="format", assigned_at=BASE, updated_at=BASE,
            )
            session.add(plan)
            session.flush()
            day = UserWorkoutPlanDay(plan_id=plan.id, day_number=1, title="Day")
            session.add(day)
            session.flush()
            block = UserWorkoutPlanBlock(
                plan_day_id=day.id, block_order=1, title="Функциональный тренинг",
                workout_format="amrap", duration_seconds=360,
            )
            exercise = Exercise(
                code="format_squat", name="Приседания", muscle_group="quads",
                primary_muscle_group="Ноги", equipment="bodyweight", hint="",
                restriction_tags="", training_environments="functional_gym",
                experience_levels="beginner", movement_pattern="squat",
                progression_type="bodyweight_reps",
            )
            session.add_all((block, exercise))
            session.flush()
            session.add(UserWorkoutPlanExercise(
                plan_day_id=day.id, plan_block_id=block.id, exercise_id=exercise.id,
                exercise_order=1, exercise_name=exercise.name,
                primary_muscle_group="Ноги", sets=1, reps_min=8, reps_max=8,
                rest_seconds=0, hint="", format_reps=8, station_order=1,
            ))
            session.commit()

        started = get_or_start_workout(self.user_id, BASE, self.db)
        self.assertTrue(started.created)
        step = get_current_step(self.user_id, started.workout.id, self.db)
        self.assertEqual("format_block", step.kind)
        self.assertEqual("amrap", step.format_block.workout_format)
        self.assertEqual(8, step.format_block.exercises[0].selected_format_reps)
        reloaded = get_active_workout(self.user_id, self.db)
        self.assertEqual(started.workout.blocks, reloaded.blocks)


if __name__ == "__main__":
    unittest.main()
