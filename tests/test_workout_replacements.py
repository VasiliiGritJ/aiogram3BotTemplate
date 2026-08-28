import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from db.migrations import run_migrations
from db.models import (
    FitnessProfile,
    SqliteSession,
    User,
    WorkoutSession,
    WorkoutSessionBlock,
    WorkoutSessionExercise,
    WorkoutSetResult,
)
from services.exercise_catalog import EXERCISE_DEFINITIONS
from services.workout_formats import start_format_block
from services.workout_plans import ensure_workout_catalog
from services.workout_progression_history import get_progression_recommendation
from services.workout_replacements import (
    ReplacementNotAllowedError,
    ReplacementReason,
    apply_replacement,
    get_replacement_options,
    rank_replacement_definitions,
)


BASE = datetime(2026, 8, 13, 12, 0, 0)


def definition(code):
    return next(item for item in EXERCISE_DEFINITIONS if item.code == code)


class WorkoutReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteSession(f"sqlite:///{(Path(self.temp.name) / 'replacement.db').as_posix()}")
        run_migrations(self.db.engine)
        ensure_workout_catalog(self.db)
        with self.db() as session:
            user = User(tg_id=12345, fullname="Replacement", username="replace", inviter_id=0)
            session.add(user)
            session.flush()
            self.user_id = user.id
            session.add(FitnessProfile(
                user_id=user.id, age=30, sex="male", height_cm=180, weight_kg=80,
                goal="muscle_gain", experience_level="beginner", training_environment="gym",
                workouts_per_week=3, session_duration_minutes=60, limitations=None,
                completed_at=BASE, updated_at=BASE,
            ))
            session.commit()

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()

    def _exercise_id(self, code):
        with self.db() as session:
            from db.models import Exercise
            return session.query(Exercise.id).filter(Exercise.code == code).scalar()

    def _snapshot(
        self,
        code="chest_press",
        *,
        mode="adaptive",
        source="generated",
        block_format=None,
        started_at=BASE,
    ):
        item = definition(code)
        exercise_id = self._exercise_id(code)
        with self.db() as session:
            workout = WorkoutSession(
                user_id=self.user_id, day_number=1, day_title="Day", status="in_progress",
                started_at=started_at, updated_at=started_at, plan_source=source, adaptation_mode=mode,
            )
            session.add(workout)
            session.flush()
            block_id = None
            if block_format is not None:
                block = WorkoutSessionBlock(
                    session_id=workout.id, block_order=1, title="Block",
                    workout_format=block_format, duration_seconds=300,
                    target_rounds=3 if block_format == "circuit_rounds" else None,
                    updated_at=BASE,
                )
                session.add(block)
                session.flush()
                block_id = block.id
            snapshot = WorkoutSessionExercise(
                session_id=workout.id, session_block_id=block_id, exercise_order=1,
                planned_exercise_id=exercise_id, planned_exercise_name=item.name,
                planned_primary_muscle_group=item.primary_muscle_label,
                planned_target_sets=3, planned_target_reps_min=8, planned_target_reps_max=12,
                planned_rest_seconds=90, planned_hint=item.hint,
                planned_progression_strategy="hypertrophy_load_reps",
                planned_format_reps=8 if block_id else None, planned_station_order=1 if block_id else None,
                selected_exercise_id=exercise_id, selected_exercise_name=item.name,
                selected_primary_muscle_group=item.primary_muscle_label,
                selected_target_sets=3, selected_target_reps_min=8, selected_target_reps_max=12,
                selected_rest_seconds=90, selected_hint=item.hint,
                selected_progression_strategy="hypertrophy_load_reps",
                selected_format_reps=8 if block_id else None, selected_station_order=1 if block_id else None,
            )
            session.add(snapshot)
            session.flush()
            session.commit()
            return workout.id, snapshot.id, block_id, exercise_id

    def test_ranking_is_deterministic_and_strictly_safe(self):
        ranked = rank_replacement_definitions(
            definition("chest_press"), training_environment="gym", experience_level="beginner"
        )
        self.assertLessEqual(len(ranked), 3)
        self.assertNotIn("chest_press", [item.code for item in ranked])
        self.assertEqual(tuple(item.code for item in ranked), tuple(sorted(item.code for item in ranked)))
        for item in ranked:
            self.assertEqual("chest", item.primary_muscle_group)
            self.assertEqual("horizontal_push", item.movement_pattern)
            self.assertEqual("external_load_reps", item.progression_type)
            self.assertIn("gym", item.environments)
            self.assertIn("beginner", item.experience_levels)

    def test_strength_and_bodyweight_environment_boundaries(self):
        strength = rank_replacement_definitions(
            definition("barbell_back_squat"), training_environment="gym", experience_level="advanced"
        )
        self.assertTrue(strength)
        self.assertTrue(all(item.equivalence_group == "barbell_squat" for item in strength))
        home = rank_replacement_definitions(
            definition("push_up"), training_environment="home", experience_level="beginner"
        )
        self.assertTrue(all(item.equipment == "bodyweight" for item in home))
        street = rank_replacement_definitions(
            definition("pull_up"), training_environment="street", experience_level="intermediate"
        )
        self.assertTrue(all("street" in item.environments for item in street))
        self.assertTrue(all(item.equipment != "machine" for item in street))

    def test_no_candidate_is_controlled_unavailable(self):
        _, snapshot_id, _, _ = self._snapshot("leg_press")
        with self.assertRaises(ReplacementNotAllowedError) as caught:
            get_replacement_options(self.user_id, snapshot_id, self.db)
        self.assertEqual(ReplacementReason.UNAVAILABLE, caught.exception.reason)

    def test_generated_replacement_preserves_planned_snapshot_and_session(self):
        workout_id, snapshot_id, _, planned_id = self._snapshot()
        options = get_replacement_options(self.user_id, snapshot_id, self.db)
        result = apply_replacement(self.user_id, snapshot_id, options.candidates[0].exercise_id, self.db)
        self.assertTrue(result.created)
        self.assertEqual(planned_id, result.planned_exercise_id)
        self.assertNotEqual(planned_id, result.selected_exercise_id)
        with self.db() as session:
            snapshot = session.get(WorkoutSessionExercise, snapshot_id)
            self.assertEqual(planned_id, snapshot.planned_exercise_id)
            self.assertEqual(result.selected_exercise_id, snapshot.selected_exercise_id)
            self.assertEqual(1, session.query(WorkoutSession).filter_by(id=workout_id).count())

    def test_replacements_and_adaptive_allow_strict_blocks(self):
        for mode, expected in (("replacements", True), ("adaptive", True), ("strict", False)):
            with self.subTest(mode=mode):
                workout_id, snapshot_id, _, _ = self._snapshot(mode=mode, source="user_defined")
                if expected:
                    self.assertTrue(get_replacement_options(self.user_id, snapshot_id, self.db).candidates)
                else:
                    with self.assertRaises(ReplacementNotAllowedError) as caught:
                        get_replacement_options(self.user_id, snapshot_id, self.db)
                    self.assertEqual(ReplacementReason.STRICT_MODE, caught.exception.reason)
                with self.db() as session:
                    workout = session.get(WorkoutSession, workout_id)
                    workout.status = "cancelled"
                    workout.finished_at = BASE
                    session.commit()

    def test_started_and_duplicate_replacement_are_safe(self):
        workout_id, snapshot_id, _, _ = self._snapshot()
        option = get_replacement_options(self.user_id, snapshot_id, self.db).candidates[0]
        self.assertTrue(apply_replacement(self.user_id, snapshot_id, option.exercise_id, self.db).created)
        self.assertFalse(apply_replacement(self.user_id, snapshot_id, option.exercise_id, self.db).created)
        with self.db() as session:
            workout = session.get(WorkoutSession, workout_id)
            workout.status = "cancelled"
            workout.finished_at = BASE
            session.commit()
        _, started_snapshot_id, _, _ = self._snapshot()
        with self.db() as session:
            session.add(WorkoutSetResult(
                session_exercise_id=started_snapshot_id, set_number=1, actual_weight_kg=20,
                actual_reps=10, completed_at=BASE,
            ))
            session.commit()
        with self.assertRaises(ReplacementNotAllowedError) as caught:
            get_replacement_options(self.user_id, started_snapshot_id, self.db)
        self.assertEqual(ReplacementReason.EXERCISE_STARTED, caught.exception.reason)
        self.assertEqual(workout_id, self._workout_id(snapshot_id))

    def _workout_id(self, snapshot_id):
        with self.db() as session:
            return session.get(WorkoutSessionExercise, snapshot_id).session_id

    def test_timed_replacement_is_allowed_only_before_start(self):
        _, snapshot_id, block_id, _ = self._snapshot(block_format="amrap")
        options = get_replacement_options(self.user_id, snapshot_id, self.db)
        self.assertTrue(options.candidates)
        start_format_block(self.user_id, block_id, BASE, self.db)
        with self.assertRaises(ReplacementNotAllowedError) as caught:
            get_replacement_options(self.user_id, snapshot_id, self.db)
        self.assertEqual(ReplacementReason.TIMED_BLOCK_STARTED, caught.exception.reason)

    def test_circuit_replacement_is_blocked_after_recorded_progress(self):
        _, snapshot_id, block_id, _ = self._snapshot(block_format="circuit_rounds")
        start_format_block(self.user_id, block_id, BASE, self.db)
        from services.workout_formats import record_completed_round
        record_completed_round(self.user_id, block_id, 0, BASE, self.db)
        with self.assertRaises(ReplacementNotAllowedError) as caught:
            get_replacement_options(self.user_id, snapshot_id, self.db)
        self.assertEqual(ReplacementReason.TIMED_BLOCK_STARTED, caught.exception.reason)

    def test_progression_uses_selected_replacement_history_not_original(self):
        dumbbell_id = self._exercise_id("dumbbell_bench_press")
        dumbbell = definition("dumbbell_bench_press")
        chest_id = self._exercise_id("chest_press")
        chest = definition("chest_press")
        with self.db() as session:
            previous = WorkoutSession(
                user_id=self.user_id, day_number=1, day_title="Previous", status="completed",
                started_at=BASE, finished_at=BASE.replace(hour=13), updated_at=BASE,
                plan_source="generated", adaptation_mode="adaptive",
            )
            session.add(previous)
            session.flush()
            selected = WorkoutSessionExercise(
                session_id=previous.id, exercise_order=1, planned_exercise_id=dumbbell_id,
                planned_exercise_name=dumbbell.name, planned_primary_muscle_group=dumbbell.primary_muscle_label,
                planned_target_sets=3, planned_target_reps_min=8, planned_target_reps_max=12,
                planned_rest_seconds=90, planned_hint=dumbbell.hint,
                selected_exercise_id=dumbbell_id, selected_exercise_name=dumbbell.name,
                selected_primary_muscle_group=dumbbell.primary_muscle_label,
                selected_target_sets=3, selected_target_reps_min=8, selected_target_reps_max=12,
                selected_rest_seconds=90, selected_hint=dumbbell.hint,
            )
            original = WorkoutSessionExercise(
                session_id=previous.id, exercise_order=2, planned_exercise_id=chest_id,
                planned_exercise_name=chest.name, planned_primary_muscle_group=chest.primary_muscle_label,
                planned_target_sets=3, planned_target_reps_min=8, planned_target_reps_max=12,
                planned_rest_seconds=90, planned_hint=chest.hint,
                selected_exercise_id=chest_id, selected_exercise_name=chest.name,
                selected_primary_muscle_group=chest.primary_muscle_label,
                selected_target_sets=3, selected_target_reps_min=8, selected_target_reps_max=12,
                selected_rest_seconds=90, selected_hint=chest.hint,
            )
            session.add_all((selected, original))
            session.flush()
            for number in range(1, 4):
                session.add(WorkoutSetResult(
                    session_exercise_id=selected.id, set_number=number,
                    actual_weight_kg=20, actual_reps=10, completed_at=BASE,
                ))
                session.add(WorkoutSetResult(
                    session_exercise_id=original.id, set_number=number,
                    actual_weight_kg=99, actual_reps=12, completed_at=BASE,
                ))
            session.commit()
        _, current_snapshot_id, _, _ = self._snapshot(started_at=BASE.replace(day=14))
        apply_replacement(self.user_id, current_snapshot_id, dumbbell_id, self.db)
        recommendation = get_progression_recommendation(
            self.user_id, current_snapshot_id, self.db
        )
        self.assertEqual((20, 20, 20), tuple(recommendation.previous_weights_kg))

    def test_other_user_cannot_read_or_apply_replacement(self):
        _, snapshot_id, _, _ = self._snapshot()
        with self.db() as session:
            user = User(tg_id=54321, fullname="Other", username="other", inviter_id=0)
            session.add(user)
            session.commit()
            other_id = user.id
        from services.workout_execution import WorkoutOwnershipError
        with self.assertRaises(WorkoutOwnershipError):
            get_replacement_options(other_id, snapshot_id, self.db)
