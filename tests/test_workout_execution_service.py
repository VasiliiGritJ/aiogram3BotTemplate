import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import (
    Exercise,
    FitnessProfile,
    SqliteSession,
    User,
    UserAccess,
    UserWorkoutPlan,
    UserWorkoutPlanDay,
    UserWorkoutPlanExercise,
    WorkoutSessionExercise,
    WorkoutTemplate,
)
from services.access import AccessStatus, get_access_decision
from services.workout_execution import (
    WorkoutAccessDeniedError,
    WorkoutExecutionError,
    WorkoutEnvironmentChangeBlockedError,
    WorkoutEnvironmentIncompatibleError,
    WorkoutResultConflictError,
    WorkoutStateError,
    WorkoutStepError,
    cancel_workout,
    change_workout_environment,
    complete_workout,
    get_active_workout,
    get_completed_workout_detail,
    get_current_step,
    get_or_start_workout,
    get_workout_history,
    get_workout_history_page,
    get_workout_exercise_technique,
    record_set_result,
)
import services.workout_execution as workout_execution
from services.workout_plans import activate_generated_plan_for_profile


BASE_TIME = datetime(2026, 8, 10, 12, 0, 0)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class WorkoutExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "workouts.db"
        self.database = SqliteSession(sqlite_url(database_path))
        run_migrations(self.database.engine)
        self.user_id = self._create_user()
        self.plan_id = self._create_assigned_plan("initial")

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    def _create_user(self) -> int:
        with self.database() as session:
            user = User(
                tg_id=123456789,
                fullname="Workout Test",
                username="workout_test",
                inviter_id=0,
            )
            session.add(user)
            session.flush()
            session.add(
                FitnessProfile(
                    user_id=user.id,
                    age=30,
                    sex="male",
                    height_cm=180,
                    weight_kg=80.0,
                    goal="muscle_gain",
                    experience_level="beginner",
                    training_environment="gym",
                    workouts_per_week=3,
                    session_duration_minutes=60,
                    limitations="Бывший дискомфорт в колене",
                    completed_at=BASE_TIME,
                    updated_at=BASE_TIME,
                )
            )
            session.add(
                UserAccess(
                    user_id=user.id,
                    trial_started_at=None,
                    trial_ends_at=None,
                    subscription_started_at=None,
                    subscription_ends_at=None,
                    updated_at=BASE_TIME,
                )
            )
            session.commit()
            return user.id

    def _create_assigned_plan(self, prefix: str) -> int:
        with self.database() as session:
            template = WorkoutTemplate(
                code=f"{prefix}_template",
                name=f"{prefix} template",
                goal="muscle_gain",
                experience_level="beginner",
                workouts_per_week=3,
                duration_bucket="standard",
                equipment="gym",
            )
            session.add(template)
            session.flush()
            plan = UserWorkoutPlan(
                user_id=self.user_id,
                template_id=template.id,
                profile_signature=f"{prefix}-signature",
                assigned_at=BASE_TIME,
                updated_at=BASE_TIME,
            )
            session.add(plan)
            session.flush()
            for day_number in range(1, 4):
                day = UserWorkoutPlanDay(
                    plan_id=plan.id,
                    day_number=day_number,
                    title=f"{prefix} day {day_number}",
                )
                session.add(day)
                session.flush()
                for exercise_order in range(1, 3):
                    exercise = Exercise(
                        code=f"{prefix}_{day_number}_{exercise_order}",
                        name=f"{prefix} exercise {day_number}.{exercise_order}",
                        muscle_group="test",
                        primary_muscle_group="тестовая группа",
                        equipment="machine",
                        variant=None,
                        alternative_name=None,
                        hint=f"{prefix} hint {day_number}.{exercise_order}",
                        restriction_tags="",
                    )
                    session.add(exercise)
                    session.flush()
                    session.add(
                        UserWorkoutPlanExercise(
                            plan_day_id=day.id,
                            exercise_id=exercise.id,
                            exercise_order=exercise_order,
                            exercise_name=exercise.name,
                            primary_muscle_group=exercise.primary_muscle_group,
                            sets=2 if exercise_order == 1 else 1,
                            reps_min=8,
                            reps_max=12,
                            rest_seconds=90,
                            hint=exercise.hint,
                            progression_strategy="hypertrophy_load_reps",
                        )
                    )
            session.commit()
            return plan.id

    def _access_dates(self) -> tuple[datetime | None, datetime | None]:
        with self.database() as session:
            access = session.get(UserAccess, self.user_id)
            return access.trial_started_at, access.trial_ends_at

    def _set_access(
        self,
        *,
        trial_started_at: datetime | None,
        trial_ends_at: datetime | None,
        subscription_started_at: datetime | None = None,
        subscription_ends_at: datetime | None = None,
    ) -> None:
        with self.database() as session:
            access = session.get(UserAccess, self.user_id)
            access.trial_started_at = trial_started_at
            access.trial_ends_at = trial_ends_at
            access.subscription_started_at = subscription_started_at
            access.subscription_ends_at = subscription_ends_at
            access.updated_at = BASE_TIME
            session.commit()

    def _finish_all_sets(self, workout_id: int, now: datetime) -> None:
        current_time = now
        while True:
            step = get_current_step(
                self.user_id,
                workout_id,
                self.database,
            )
            if step.ready_to_complete:
                return
            record_set_result(
                self.user_id,
                workout_id,
                step.exercise.id,
                step.set_number,
                0,
                10,
                current_time,
                self.database,
            )
            current_time += timedelta(minutes=1)

    def _complete_started_workout(self, now: datetime):
        started = get_or_start_workout(self.user_id, now, self.database)
        self._finish_all_sets(started.workout.id, now + timedelta(minutes=1))
        return complete_workout(
            self.user_id,
            started.workout.id,
            now + timedelta(minutes=10),
            self.database,
        )

    def test_trial_available_start_is_atomic_and_creates_snapshot(self) -> None:
        result = get_or_start_workout(self.user_id, BASE_TIME, self.database)

        self.assertTrue(result.created)
        self.assertTrue(result.trial_activated)
        self.assertEqual(1, result.workout.day_number)
        self.assertEqual([1, 2], [item.exercise_order for item in result.workout.exercises])
        self.assertTrue(
            all(
                item.planned_exercise_id == item.selected_exercise_id
                and item.planned_exercise_name == item.selected_exercise_name
                and item.planned_target_sets == item.selected_target_sets
                and item.planned_progression_strategy
                == item.selected_progression_strategy
                == "hypertrophy_load_reps"
                for item in result.workout.exercises
            )
        )
        self.assertEqual(
            (BASE_TIME, BASE_TIME + timedelta(days=3)),
            self._access_dates(),
        )

    def test_profile_change_refreshes_generated_plan_before_new_workout(self) -> None:
        completed = self._complete_started_workout(BASE_TIME)
        old_plan_id = completed.source_plan_id
        old_names = tuple(item.selected_exercise_name for item in completed.exercises)
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.training_environment = "gym"
            profile.experience_level = "beginner"
            profile.limitations = None
            profile.updated_at = BASE_TIME + timedelta(days=1)
            session.commit()

        activated = activate_generated_plan_for_profile(
            self.user_id,
            self.database,
            create_if_missing=False,
        )
        started = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(days=1),
            self.database,
        )
        current = workout_execution.get_active_workout(self.user_id, self.database)
        history = get_workout_history(self.user_id, self.database)

        self.assertNotEqual(old_plan_id, started.workout.source_plan_id)
        self.assertEqual(activated.plan.id, started.workout.source_plan_id)
        self.assertEqual(started.workout.source_plan_id, current.source_plan_id)
        self.assertEqual(old_names, tuple(
            item.selected_exercise_name for item in history[0].exercises
        ))
        with self.database() as session:
            self.assertIsNone(session.get(UserWorkoutPlan, old_plan_id))
            self.assertEqual(
                1,
                session.scalar(select(func.count(UserWorkoutPlan.id))),
            )

    def test_session_environment_override_persists_without_changing_profile_default(self) -> None:
        activated = activate_generated_plan_for_profile(
            self.user_id,
            self.database,
        )
        started = get_or_start_workout(
            self.user_id,
            BASE_TIME,
            self.database,
            training_environment="street",
        )
        resumed = get_active_workout(self.user_id, self.database)

        self.assertEqual(activated.plan.id, started.workout.source_plan_id)
        self.assertEqual("street", started.workout.effective_training_environment)
        self.assertEqual("street", resumed.effective_training_environment)
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            selected_ids = [item.selected_exercise_id for item in resumed.exercises]
            selected = session.scalars(
                select(Exercise).where(Exercise.id.in_(selected_ids))
            ).all()
        self.assertEqual("gym", profile.training_environment)
        self.assertTrue(all(
            "street" in exercise.training_environments.split(",")
            for exercise in selected
        ))

        cancel_workout(
            self.user_id,
            started.workout.id,
            BASE_TIME + timedelta(minutes=1),
            self.database,
        )
        next_workout = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(minutes=2),
            self.database,
        )
        self.assertEqual("gym", next_workout.workout.effective_training_environment)

    def test_generated_gym_plan_refuses_home_override_without_a_true_pull_substitute(self) -> None:
        activate_generated_plan_for_profile(self.user_id, self.database)

        with self.assertRaises(WorkoutEnvironmentIncompatibleError):
            get_or_start_workout(
                self.user_id,
                BASE_TIME,
                self.database,
                training_environment="home",
            )
        self.assertIsNone(get_active_workout(self.user_id, self.database))

    def test_advanced_strength_home_override_fails_before_snapshot_creation(self) -> None:
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
            profile.goal = "strength"
            profile.experience_level = "advanced"
            profile.training_environment = "gym"
            session.commit()
        activate_generated_plan_for_profile(self.user_id, self.database)

        with self.assertRaisesRegex(
            WorkoutEnvironmentIncompatibleError,
            "недостаточно безопасных вариантов",
        ):
            get_or_start_workout(
                self.user_id,
                BASE_TIME,
                self.database,
                training_environment="home",
            )
        self.assertIsNone(get_active_workout(self.user_id, self.database))
        with self.database() as session:
            profile = session.get(FitnessProfile, self.user_id)
        self.assertEqual("gym", profile.training_environment)

    def test_environment_change_is_blocked_after_first_recorded_set(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        step = get_current_step(self.user_id, started.workout.id, self.database)
        record_set_result(
            self.user_id,
            started.workout.id,
            step.exercise.id,
            step.set_number,
            10,
            10,
            BASE_TIME,
            self.database,
        )

        with self.assertRaises(WorkoutEnvironmentChangeBlockedError):
            change_workout_environment(
                self.user_id,
                started.workout.id,
                "home",
                self.database,
            )

    def test_strict_user_program_refuses_incompatible_environment(self) -> None:
        with self.database() as session:
            plan = session.get(UserWorkoutPlan, self.plan_id)
            plan.plan_source = "user_defined"
            plan.adaptation_mode = "strict"
            session.commit()

        with self.assertRaises(WorkoutEnvironmentIncompatibleError):
            get_or_start_workout(
                self.user_id,
                BASE_TIME,
                self.database,
                training_environment="home",
            )
        self.assertIsNone(get_active_workout(self.user_id, self.database))

    def test_technique_requires_owned_controlled_snapshot(self) -> None:
        with self.database() as session:
            exercise = session.scalar(select(Exercise).limit(1))
            exercise.code = "bird_dog"
            session.commit()
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        snapshot = started.workout.exercises[0]

        technique = get_workout_exercise_technique(
            self.user_id,
            snapshot.id,
            self.database,
        )

        self.assertTrue(technique.start_position)
        self.assertTrue(technique.action)
        self.assertTrue(technique.control)

    def test_resume_keeps_trial_dates_even_after_expiration(self) -> None:
        first = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        dates_before = self._access_dates()

        repeated = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(days=4),
            self.database,
        )

        self.assertFalse(repeated.created)
        self.assertFalse(repeated.trial_activated)
        self.assertEqual(first.workout.id, repeated.workout.id)
        self.assertEqual(dates_before, self._access_dates())

    def test_active_trial_and_subscription_start_without_changing_dates(self) -> None:
        trial_start = BASE_TIME - timedelta(days=1)
        trial_end = BASE_TIME + timedelta(days=2)
        self._set_access(
            trial_started_at=trial_start,
            trial_ends_at=trial_end,
        )

        trial_result = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        self.assertTrue(trial_result.created)
        self.assertFalse(trial_result.trial_activated)
        self.assertEqual((trial_start, trial_end), self._access_dates())

        cancel_workout(
            self.user_id,
            trial_result.workout.id,
            BASE_TIME + timedelta(minutes=1),
            self.database,
        )
        subscription_start = BASE_TIME - timedelta(days=1)
        subscription_end = BASE_TIME + timedelta(days=30)
        self._set_access(
            trial_started_at=BASE_TIME - timedelta(days=10),
            trial_ends_at=BASE_TIME - timedelta(days=7),
            subscription_started_at=subscription_start,
            subscription_ends_at=subscription_end,
        )

        subscription_result = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(days=1),
            self.database,
        )
        self.assertTrue(subscription_result.created)
        self.assertFalse(subscription_result.trial_activated)
        self.assertEqual(
            AccessStatus.ACTIVE,
            get_access_decision(
                self.user_id,
                BASE_TIME + timedelta(days=1),
                self.database,
            ).status,
        )

    def test_expired_access_cannot_start_new_workout(self) -> None:
        self._set_access(
            trial_started_at=BASE_TIME - timedelta(days=5),
            trial_ends_at=BASE_TIME - timedelta(days=2),
        )

        with self.assertRaises(WorkoutAccessDeniedError):
            get_or_start_workout(self.user_id, BASE_TIME, self.database)
        self.assertIsNone(get_active_workout(self.user_id, self.database))

    def test_snapshot_creation_failure_rolls_back_trial_activation(self) -> None:
        with patch.object(
            workout_execution,
            "_create_workout_session",
            side_effect=WorkoutExecutionError("forced snapshot failure"),
        ):
            with self.assertRaises(WorkoutExecutionError):
                get_or_start_workout(self.user_id, BASE_TIME, self.database)

        self.assertEqual((None, None), self._access_dates())
        self.assertIsNone(get_active_workout(self.user_id, self.database))

    def test_snapshot_is_immutable_after_source_plan_change(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        original_name = started.workout.exercises[0].selected_exercise_name
        with self.database() as session:
            source = session.scalar(
                select(UserWorkoutPlanExercise)
                .where(
                    UserWorkoutPlanExercise.plan_day_id
                    == started.workout.source_plan_day_id
                )
                .order_by(UserWorkoutPlanExercise.exercise_order)
            )
            source.exercise_name = "Changed source name"
            session.commit()

        active = get_active_workout(self.user_id, self.database)
        self.assertEqual(original_name, active.exercises[0].selected_exercise_name)

    def test_race_recovery_returns_existing_active_session(self) -> None:
        first = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        original_lookup = workout_execution._get_active_workout_in_session
        calls = 0

        def stale_first_lookup(session, user_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return original_lookup(session, user_id)

        with patch.object(
            workout_execution,
            "_get_active_workout_in_session",
            side_effect=stale_first_lookup,
        ):
            raced = get_or_start_workout(
                self.user_id,
                BASE_TIME + timedelta(minutes=1),
                self.database,
            )

        self.assertFalse(raced.created)
        self.assertEqual(first.workout.id, raced.workout.id)

    def test_completed_cycle_advances_and_wraps_days(self) -> None:
        first = self._complete_started_workout(BASE_TIME)
        second = self._complete_started_workout(BASE_TIME + timedelta(hours=1))
        third = self._complete_started_workout(BASE_TIME + timedelta(hours=2))
        wrapped = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(hours=3),
            self.database,
        )

        self.assertEqual([1, 2, 3], [first.day_number, second.day_number, third.day_number])
        self.assertEqual(1, wrapped.workout.day_number)

    def test_cancelled_session_does_not_advance_cycle(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        cancelled = cancel_workout(
            self.user_id,
            started.workout.id,
            BASE_TIME + timedelta(minutes=1),
            self.database,
        )
        next_started = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(minutes=2),
            self.database,
        )

        self.assertEqual("cancelled", cancelled.status)
        self.assertEqual(1, next_started.workout.day_number)

    def test_new_assigned_plan_restarts_cycle_at_day_one(self) -> None:
        completed = self._complete_started_workout(BASE_TIME)
        with self.database() as session:
            session.delete(session.get(UserWorkoutPlan, completed.source_plan_id))
            session.commit()
        self.plan_id = self._create_assigned_plan("replacement")

        restarted = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(hours=1),
            self.database,
        )
        self.assertEqual(1, restarted.workout.day_number)

    def test_current_step_and_recording_follow_selected_snapshot(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        first_step = get_current_step(self.user_id, started.workout.id, self.database)
        self.assertEqual("record_set", first_step.kind)
        self.assertEqual(1, first_step.exercise.exercise_order)
        self.assertEqual(1, first_step.set_number)

        with self.assertRaises(WorkoutStepError):
            record_set_result(
                self.user_id,
                started.workout.id,
                started.workout.exercises[1].id,
                1,
                0,
                10,
                BASE_TIME,
                self.database,
            )
        first_saved = record_set_result(
            self.user_id,
            started.workout.id,
            first_step.exercise.id,
            1,
            0,
            10,
            BASE_TIME,
            self.database,
        )
        self.assertTrue(first_saved.created)
        self.assertEqual(2, first_saved.next_step.set_number)

        second_saved = record_set_result(
            self.user_id,
            started.workout.id,
            first_step.exercise.id,
            2,
            10,
            10,
            BASE_TIME + timedelta(minutes=1),
            self.database,
        )
        self.assertEqual(2, second_saved.next_step.exercise.exercise_order)

    def test_result_validation_and_duplicate_semantics(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        step = get_current_step(self.user_id, started.workout.id, self.database)
        for weight, reps in ((-1, 10), (10, 0)):
            with self.subTest(weight=weight, reps=reps):
                with self.assertRaises(WorkoutStepError):
                    record_set_result(
                        self.user_id, started.workout.id, step.exercise.id,
                        1, weight, reps, BASE_TIME, self.database,
                    )
        first = record_set_result(
            self.user_id, started.workout.id, step.exercise.id,
            1, 0, 10, BASE_TIME, self.database,
        )
        repeated = record_set_result(
            self.user_id, started.workout.id, step.exercise.id,
            1, 0, 10, BASE_TIME + timedelta(minutes=1), self.database,
        )
        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        self.assertEqual(first.result.id, repeated.result.id)
        with self.assertRaises(WorkoutResultConflictError):
            record_set_result(
                self.user_id, started.workout.id, step.exercise.id,
                1, 5, 10, BASE_TIME, self.database,
            )

    def test_current_step_uses_selected_target_sets_for_future_swap(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        first_exercise = started.workout.exercises[0]
        with self.database() as session:
            snapshot = session.get(WorkoutSessionExercise, first_exercise.id)
            snapshot.selected_target_sets = 1
            session.commit()

        record_set_result(
            self.user_id, started.workout.id, first_exercise.id,
            1, 0, 10, BASE_TIME, self.database,
        )
        next_step = get_current_step(self.user_id, started.workout.id, self.database)
        self.assertEqual(2, next_step.exercise.exercise_order)
        with self.assertRaises(WorkoutStepError):
            record_set_result(
                self.user_id, started.workout.id, first_exercise.id,
                2, 0, 10, BASE_TIME, self.database,
            )

    def test_complete_after_expiration_is_idempotent_and_preserves_trial(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        dates_before = self._access_dates()
        with self.assertRaises(WorkoutStateError):
            complete_workout(
                self.user_id, started.workout.id, BASE_TIME, self.database
            )
        self._finish_all_sets(started.workout.id, BASE_TIME + timedelta(minutes=1))
        completed = complete_workout(
            self.user_id,
            started.workout.id,
            BASE_TIME + timedelta(days=4),
            self.database,
        )
        repeated = complete_workout(
            self.user_id,
            started.workout.id,
            BASE_TIME + timedelta(days=5),
            self.database,
        )

        self.assertEqual("completed", completed.status)
        self.assertEqual(completed.finished_at, repeated.finished_at)
        self.assertEqual(dates_before, self._access_dates())

    def test_history_uses_snapshots_after_source_plan_is_deleted(self) -> None:
        completed = self._complete_started_workout(BASE_TIME)
        with self.database() as session:
            session.delete(session.get(UserWorkoutPlan, completed.source_plan_id))
            session.commit()

        history = get_workout_history(self.user_id, self.database)
        self.assertEqual(1, len(history))
        self.assertEqual("completed", history[0].status)
        self.assertEqual(2, len(history[0].exercises))
        self.assertTrue(history[0].exercises[0].set_results)
        self.assertIsNone(history[0].source_plan_id)

        detail = get_completed_workout_detail(
            self.user_id,
            completed.id,
            self.database,
        )
        self.assertEqual("completed", detail.status)
        self.assertTrue(detail.exercises[0].set_results)

    def test_history_page_is_bounded_and_excludes_cancelled_sessions(self) -> None:
        self._set_access(
            trial_started_at=None,
            trial_ends_at=None,
            subscription_started_at=BASE_TIME - timedelta(days=1),
            subscription_ends_at=BASE_TIME + timedelta(days=30),
        )
        completed = [
            self._complete_started_workout(BASE_TIME + timedelta(hours=index))
            for index in range(6)
        ]
        cancelled = get_or_start_workout(
            self.user_id,
            BASE_TIME + timedelta(hours=7),
            self.database,
        )
        cancel_workout(
            self.user_id,
            cancelled.workout.id,
            BASE_TIME + timedelta(hours=7, minutes=1),
            self.database,
        )

        first_page = get_workout_history_page(
            self.user_id,
            offset=0,
            page_size=5,
            session_factory=self.database,
        )
        second_page = get_workout_history_page(
            self.user_id,
            offset=5,
            page_size=5,
            session_factory=self.database,
        )

        self.assertEqual(5, len(first_page.workouts))
        self.assertTrue(first_page.has_older)
        self.assertFalse(first_page.has_newer)
        self.assertNotIn(completed[0].id, [item.id for item in first_page.workouts])
        self.assertEqual([completed[0].id], [item.id for item in second_page.workouts])
        self.assertFalse(second_page.has_older)
        self.assertTrue(second_page.has_newer)
        self.assertNotIn(cancelled.workout.id, [item.id for item in first_page.workouts])

    def test_completed_detail_rejects_foreign_and_cancelled_sessions(self) -> None:
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        cancel_workout(
            self.user_id,
            started.workout.id,
            BASE_TIME + timedelta(minutes=1),
            self.database,
        )
        with self.assertRaises(WorkoutStateError):
            get_completed_workout_detail(
                self.user_id,
                started.workout.id,
                self.database,
            )
        with self.assertRaises(WorkoutExecutionError):
            get_completed_workout_detail(999999, started.workout.id, self.database)

    def test_cancelled_workout_is_excluded_from_history_and_profile_plan_stay_unchanged(self) -> None:
        with self.database() as session:
            profile_before = session.get(FitnessProfile, self.user_id).limitations
            plan_before = session.get(UserWorkoutPlan, self.user_id).profile_signature
        started = get_or_start_workout(self.user_id, BASE_TIME, self.database)
        cancel_workout(
            self.user_id, started.workout.id, BASE_TIME + timedelta(minutes=1), self.database
        )
        with self.database() as session:
            profile_after = session.get(FitnessProfile, self.user_id).limitations
            plan_after = session.get(UserWorkoutPlan, self.user_id).profile_signature

        self.assertEqual((), get_workout_history(self.user_id, self.database))
        self.assertEqual(profile_before, profile_after)
        self.assertEqual(plan_before, plan_after)


if __name__ == "__main__":
    unittest.main()
