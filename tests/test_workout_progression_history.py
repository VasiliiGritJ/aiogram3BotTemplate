import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import (
    Exercise,
    SqliteSession,
    User,
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSetResult,
)
from services.workout_progression import ProgressionReason, ProgressionStrategy
from services.workout_progression_history import (
    ProgressionOwnershipError,
    ProgressionSnapshotNotFoundError,
    get_progression_recommendation,
)


BASE_TIME = datetime(2026, 8, 11, 12, 0, 0)
_UNSET = object()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class WorkoutProgressionHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "progression.db"
        self.database = SqliteSession(sqlite_url(database_path))
        run_migrations(self.database.engine)
        self.user_id = self._create_user(1001)
        self.other_user_id = self._create_user(1002)
        self.primary_exercise_id = self._create_exercise("primary")
        self.alternative_exercise_id = self._create_exercise("alternative")

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    def _create_user(self, tg_id: int) -> int:
        with self.database() as session:
            user = User(
                tg_id=tg_id,
                fullname=f"Progression User {tg_id}",
                username=f"progression_{tg_id}",
                inviter_id=0,
            )
            session.add(user)
            session.commit()
            return user.id

    def _create_exercise(self, code: str) -> int:
        with self.database() as session:
            exercise = Exercise(
                code=code,
                name=code,
                muscle_group="test",
                primary_muscle_group="тест",
                equipment="machine",
                variant=None,
                alternative_name=None,
                hint="test hint",
                restriction_tags="",
            )
            session.add(exercise)
            session.commit()
            return exercise.id

    def _add_snapshot(
        self,
        *,
        user_id: int,
        status: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        selected_exercise_id: int | None | object = _UNSET,
        planned_exercise_id: int | None | object = _UNSET,
        target_sets: int = 3,
        reps_min: int = 8,
        reps_max: int = 12,
        results: tuple[tuple[int, float, int], ...] = (),
        exercise_order: int = 1,
        progression_strategy: str | None = None,
    ) -> int:
        if status in {"completed", "cancelled"} and finished_at is None:
            finished_at = started_at + timedelta(minutes=30)
        with self.database() as session:
            workout = WorkoutSession(
                user_id=user_id,
                source_plan_id=None,
                source_plan_day_id=None,
                day_number=1,
                day_title="Тестовая тренировка",
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                updated_at=finished_at or started_at,
            )
            session.add(workout)
            session.flush()
            snapshot = WorkoutSessionExercise(
                session_id=workout.id,
                source_plan_exercise_id=None,
                exercise_order=exercise_order,
                planned_exercise_id=(
                    self.primary_exercise_id
                    if planned_exercise_id is _UNSET
                    else planned_exercise_id
                ),
                planned_exercise_name="planned",
                planned_primary_muscle_group="тест",
                planned_target_sets=target_sets,
                planned_target_reps_min=reps_min,
                planned_target_reps_max=reps_max,
                planned_rest_seconds=90,
                planned_hint="planned hint",
                planned_progression_strategy=progression_strategy,
                selected_exercise_id=(
                    self.primary_exercise_id
                    if selected_exercise_id is _UNSET
                    else selected_exercise_id
                ),
                selected_exercise_name="selected",
                selected_primary_muscle_group="тест",
                selected_target_sets=target_sets,
                selected_target_reps_min=reps_min,
                selected_target_reps_max=reps_max,
                selected_rest_seconds=90,
                selected_hint="selected hint",
                selected_progression_strategy=progression_strategy,
            )
            session.add(snapshot)
            session.flush()
            for set_number, weight, reps in results:
                session.add(
                    WorkoutSetResult(
                        session_exercise_id=snapshot.id,
                        set_number=set_number,
                        actual_weight_kg=weight,
                        actual_reps=reps,
                        completed_at=finished_at or started_at,
                    )
                )
            session.commit()
            return snapshot.id

    def _current_snapshot(self, **kwargs: object) -> int:
        return self._add_snapshot(
            user_id=self.user_id,
            status="in_progress",
            started_at=BASE_TIME,
            **kwargs,
        )

    def _completed_snapshot(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        results: tuple[tuple[int, float, int], ...],
        **kwargs: object,
    ) -> int:
        return self._add_snapshot(
            user_id=self.user_id,
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            results=results,
            **kwargs,
        )

    def _recommendation_for_current(self, **kwargs: object):
        current = self._current_snapshot(**kwargs)
        return get_progression_recommendation(self.user_id, current, self.database)

    def test_latest_completed_snapshot_is_used_as_baseline(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=4),
            finished_at=BASE_TIME - timedelta(days=3),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 30, 12), (2, 30, 12), (3, 30, 12)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual(ProgressionReason.INCREASE_WEIGHT, recommendation.reason)
        self.assertEqual((Decimal("30"),) * 3, recommendation.previous_weights_kg)
        self.assertEqual(Decimal("31.5"), recommendation.suggested_weight_kg)

    def test_strength_snapshot_uses_conservative_strategy(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            target_sets=3,
            reps_min=3,
            reps_max=6,
            results=((1, 100, 6), (2, 100, 6), (3, 100, 6)),
        )

        recommendation = self._recommendation_for_current(
            target_sets=3,
            reps_min=3,
            reps_max=6,
            progression_strategy=ProgressionStrategy.STRENGTH_LOAD_REPS,
        )

        self.assertEqual(
            ProgressionReason.STRENGTH_INCREASE_WEIGHT,
            recommendation.reason,
        )
        self.assertEqual(Decimal("102.5"), recommendation.suggested_weight_kg)
        self.assertEqual(
            ProgressionStrategy.STRENGTH_LOAD_REPS,
            recommendation.strategy,
        )

    def test_legacy_null_strategy_preserves_hypertrophy_semantics(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )

        recommendation = self._recommendation_for_current(
            progression_strategy=None,
        )

        self.assertEqual(ProgressionReason.INCREASE_WEIGHT, recommendation.reason)
        self.assertEqual(
            ProgressionStrategy.HYPERTROPHY_LOAD_REPS,
            recommendation.strategy,
        )

    def test_bodyweight_history_uses_only_explicit_catalog_successor(self) -> None:
        incline_push_up_id = self._create_exercise("incline_push_up")
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            selected_exercise_id=incline_push_up_id,
            results=((1, 0, 12), (2, 0, 12), (3, 0, 12)),
        )

        recommendation = self._recommendation_for_current(
            selected_exercise_id=incline_push_up_id,
            progression_strategy=ProgressionStrategy.BODYWEIGHT_REPS,
        )

        self.assertEqual(
            ProgressionReason.BODYWEIGHT_ADVANCE_VARIATION,
            recommendation.reason,
        )
        self.assertEqual("push_up", recommendation.suggested_exercise_code)
        self.assertEqual(Decimal("0"), recommendation.suggested_weight_kg)

    def test_cancelled_and_in_progress_snapshots_are_ignored(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=5),
            finished_at=BASE_TIME - timedelta(days=4),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._add_snapshot(
            user_id=self.user_id,
            status="cancelled",
            started_at=BASE_TIME - timedelta(days=3),
            finished_at=BASE_TIME - timedelta(days=2),
            results=((1, 50, 12), (2, 50, 12), (3, 50, 12)),
        )
        current = self._add_snapshot(
            user_id=self.user_id,
            status="completed",
            started_at=BASE_TIME,
            finished_at=BASE_TIME + timedelta(minutes=30),
        )
        self._add_snapshot(
            user_id=self.user_id,
            status="in_progress",
            started_at=BASE_TIME - timedelta(days=1),
            results=((1, 60, 12),),
        )

        recommendation = get_progression_recommendation(self.user_id, current, self.database)

        self.assertEqual(ProgressionReason.INCREASE_WEIGHT, recommendation.reason)
        self.assertEqual((Decimal("20"),) * 3, recommendation.previous_weights_kg)

    def test_history_finished_after_current_start_is_ignored(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=3),
            finished_at=BASE_TIME - timedelta(days=2),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME + timedelta(hours=1),
            finished_at=BASE_TIME + timedelta(hours=2),
            results=((1, 50, 12), (2, 50, 12), (3, 50, 12)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual((Decimal("20"),) * 3, recommendation.previous_weights_kg)

    def test_ownership_and_missing_snapshot_are_rejected(self) -> None:
        foreign = self._add_snapshot(
            user_id=self.other_user_id,
            status="in_progress",
            started_at=BASE_TIME,
        )

        with self.assertRaises(ProgressionOwnershipError):
            get_progression_recommendation(self.user_id, foreign, self.database)
        with self.assertRaises(ProgressionSnapshotNotFoundError):
            get_progression_recommendation(self.user_id, 999999, self.database)

    def test_selected_exercise_wins_over_planned_exercise(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=3),
            finished_at=BASE_TIME - timedelta(days=2),
            selected_exercise_id=self.alternative_exercise_id,
            planned_exercise_id=self.primary_exercise_id,
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            selected_exercise_id=self.primary_exercise_id,
            planned_exercise_id=self.primary_exercise_id,
            results=((1, 50, 12), (2, 50, 12), (3, 50, 12)),
        )

        recommendation = self._recommendation_for_current(
            selected_exercise_id=self.alternative_exercise_id,
            planned_exercise_id=self.primary_exercise_id,
        )

        self.assertEqual((Decimal("20"),) * 3, recommendation.previous_weights_kg)

    def test_alternative_history_is_separate_from_original(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=3),
            finished_at=BASE_TIME - timedelta(days=2),
            selected_exercise_id=self.primary_exercise_id,
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            selected_exercise_id=self.alternative_exercise_id,
            results=((1, 50, 12), (2, 50, 12), (3, 50, 12)),
        )

        recommendation = self._recommendation_for_current(
            selected_exercise_id=self.primary_exercise_id,
        )

        self.assertEqual((Decimal("20"),) * 3, recommendation.previous_weights_kg)

    def test_null_selected_exercise_id_returns_no_history_without_name_matching(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        current = self._add_snapshot(
            user_id=self.user_id,
            status="in_progress",
            started_at=BASE_TIME,
            selected_exercise_id=None,
        )

        recommendation = get_progression_recommendation(self.user_id, current, self.database)

        self.assertEqual(ProgressionReason.NO_HISTORY, recommendation.reason)

    def test_incomplete_latest_snapshot_is_not_replaced_by_older_history(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=4),
            finished_at=BASE_TIME - timedelta(days=3),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 30, 12), (2, 30, 12)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual(ProgressionReason.INSUFFICIENT_DATA, recommendation.reason)

    def test_latest_incompatible_set_count_is_not_replaced_by_older_history(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=4),
            finished_at=BASE_TIME - timedelta(days=3),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            target_sets=2,
            results=((1, 30, 12), (2, 30, 12)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual(ProgressionReason.INSUFFICIENT_DATA, recommendation.reason)

    def test_latest_incompatible_rep_range_is_not_replaced_by_older_history(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=4),
            finished_at=BASE_TIME - timedelta(days=3),
            results=((1, 20, 12), (2, 20, 12), (3, 20, 12)),
        )
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            reps_min=10,
            reps_max=15,
            results=((1, 30, 15), (2, 30, 15), (3, 30, 15)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual(ProgressionReason.INSUFFICIENT_DATA, recommendation.reason)

    def test_actual_weight_is_the_next_baseline(self) -> None:
        self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 12.5, 12), (2, 12.5, 12), (3, 12.5, 12)),
        )

        recommendation = self._recommendation_for_current()

        self.assertEqual((Decimal("12.5"),) * 3, recommendation.previous_weights_kg)
        self.assertEqual(Decimal("13.0"), recommendation.suggested_weight_kg)

    def test_resume_is_deterministic_and_service_does_not_mutate_data(self) -> None:
        historical = self._completed_snapshot(
            started_at=BASE_TIME - timedelta(days=2),
            finished_at=BASE_TIME - timedelta(days=1),
            results=((1, 20, 12), (2, 20, 10), (3, 20, 8)),
        )
        current = self._current_snapshot()
        with self.database() as session:
            before = (
                session.scalar(select(func.count(WorkoutSession.id))),
                session.scalar(select(func.count(WorkoutSessionExercise.id))),
                session.scalar(select(func.count(WorkoutSetResult.id))),
                session.get(WorkoutSessionExercise, historical).selected_exercise_id,
                session.get(WorkoutSessionExercise, current).selected_target_sets,
            )

        first = get_progression_recommendation(self.user_id, current, self.database)
        second = get_progression_recommendation(self.user_id, current, self.database)

        with self.database() as session:
            after = (
                session.scalar(select(func.count(WorkoutSession.id))),
                session.scalar(select(func.count(WorkoutSessionExercise.id))),
                session.scalar(select(func.count(WorkoutSetResult.id))),
                session.get(WorkoutSessionExercise, historical).selected_exercise_id,
                session.get(WorkoutSessionExercise, current).selected_target_sets,
            )

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(ProgressionReason.HOLD_ADD_REPS, first.reason)


if __name__ == "__main__":
    unittest.main()
