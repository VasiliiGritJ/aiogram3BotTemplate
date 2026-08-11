import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import AsyncMock, patch

from services.access import AccessDecision, AccessStatus
from services.workout_execution import (
    CurrentWorkoutStep,
    WorkoutAccessDeniedError,
    WorkoutHistoryPage,
    WorkoutNotFoundError,
    WorkoutSetResultView,
    WorkoutSessionExerciseView,
    WorkoutSessionView,
    WorkoutStartResult,
    WorkoutStateError,
)
from services.workout_progression import ProgressionReason, ProgressionRecommendation


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler

    def message(self, *args, **kwargs):
        return lambda handler: handler


def _load_handler_module():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    config.bot = object()
    config.admins = []
    config.bot_token = "test-token"
    config.DEBUG_MODE = False
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop("handlers.workout_execution", None)
        return importlib.import_module("handlers.workout_execution")


def _load_handler_module_with_fake_config(module_name: str):
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    config.bot = object()
    config.admins = []
    config.bot_token = "test-token"
    config.DEBUG_MODE = False
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop(module_name, None)
        return importlib.import_module(module_name)


workout_ui = _load_handler_module()


@dataclass
class _User:
    id: int = 7


@dataclass
class _FromUser:
    id: int = 101


class _Message:
    def __init__(self, text: str = "", user_id: int = 101) -> None:
        self.text = text
        self.from_user = _FromUser(user_id)
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


class _Call:
    def __init__(
        self,
        message: _Message | None = None,
        user_id: int = 101,
        data: str = "",
    ) -> None:
        self.message = message or _Message(user_id=user_id)
        self.from_user = _FromUser(user_id)
        self.data = data
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs) -> None:
        self.answers.append((args, kwargs))


class _State:
    def __init__(self) -> None:
        self.data: dict = {}
        self.current_state = None
        self.clear_count = 0

    async def clear(self) -> None:
        self.data.clear()
        self.current_state = None
        self.clear_count += 1

    async def update_data(self, **data) -> None:
        self.data.update(data)

    async def set_state(self, state) -> None:
        self.current_state = state

    async def get_data(self) -> dict:
        return dict(self.data)


def _exercise(
    *,
    sets: int = 2,
    exercise_id: int = 31,
    order: int = 1,
    name: str = "Жим",
    results: tuple[WorkoutSetResultView, ...] = (),
) -> WorkoutSessionExerciseView:
    return WorkoutSessionExerciseView(
        id=exercise_id,
        exercise_order=order,
        planned_exercise_id=1,
        planned_exercise_name=name,
        planned_primary_muscle_group="Грудь",
        planned_target_sets=sets,
        planned_target_reps_min=8,
        planned_target_reps_max=12,
        planned_rest_seconds=90,
        planned_hint="Контролируйте движение",
        selected_exercise_id=1,
        selected_exercise_name=name,
        selected_primary_muscle_group="Грудь",
        selected_target_sets=sets,
        selected_target_reps_min=8,
        selected_target_reps_max=12,
        selected_rest_seconds=90,
        selected_hint="Контролируйте движение",
        set_results=results,
    )


def _workout(
    *,
    status: str = "in_progress",
    workout_id: int = 21,
    day_number: int = 1,
    finished_at: datetime | None = None,
    exercises: tuple[WorkoutSessionExerciseView, ...] | None = None,
) -> WorkoutSessionView:
    started = datetime(2026, 8, 10, 12, 0)
    return WorkoutSessionView(
        id=workout_id,
        user_id=7,
        source_plan_id=1,
        source_plan_day_id=1,
        day_number=day_number,
        day_title="Верх тела",
        status=status,
        started_at=started,
        finished_at=finished_at,
        updated_at=started,
        exercises=exercises or (_exercise(),),
    )


def _step(*, ready: bool = False, set_number: int = 1) -> CurrentWorkoutStep:
    return CurrentWorkoutStep(
        kind="ready_to_complete" if ready else "record_set",
        workout_id=21,
        exercise=None if ready else _exercise(),
        set_number=None if ready else set_number,
    )


def _recommendation(
    reason: ProgressionReason,
    *,
    suggested_weight: str | None = None,
    suggested_reps: tuple[int, ...] | None = None,
    previous_weights: tuple[str, ...] = (),
    previous_reps: tuple[int, ...] = (),
) -> ProgressionRecommendation:
    return ProgressionRecommendation(
        reason=reason,
        suggested_weight_kg=(
            None if suggested_weight is None else Decimal(suggested_weight)
        ),
        suggested_reps=suggested_reps,
        previous_weights_kg=tuple(Decimal(weight) for weight in previous_weights),
        previous_reps=previous_reps,
    )


class WorkoutExecutionUiTests(unittest.TestCase):
    def run_async(self, coroutine) -> None:
        asyncio.run(coroutine)

    @staticmethod
    def callback_values(markup) -> list[str | None]:
        return [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]

    def test_entry_actions_do_not_start_trial_and_cover_access_states(self) -> None:
        with (
            patch.object(workout_ui, "get_active_workout", return_value=None),
            patch.object(
                workout_ui,
                "get_access_decision",
                return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            ),
            patch.object(workout_ui, "get_or_start_workout") as start,
        ):
            self.assertEqual(
                ("🏋️ Начать первую тренировку бесплатно", "workout:start"),
                workout_ui.workout_entry_action(7),
            )
            self.assertIn(
                "workout:start",
                self.callback_values(workout_ui.workout_menu_markup(7)),
            )
            start.assert_not_called()

        with (
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "get_access_decision") as decision,
        ):
            self.assertEqual(
                ("▶️ Продолжить тренировку", "workout:resume"),
                workout_ui.workout_entry_action(7),
            )
            decision.assert_not_called()

        with (
            patch.object(workout_ui, "get_active_workout", return_value=None),
            patch.object(
                workout_ui,
                "get_access_decision",
                return_value=AccessDecision(AccessStatus.TRIAL, None),
            ),
        ):
            self.assertEqual(
                ("🏋️ Начать тренировку", "workout:start"),
                workout_ui.workout_entry_action(7),
            )

        with (
            patch.object(workout_ui, "get_active_workout", return_value=None),
            patch.object(
                workout_ui,
                "get_access_decision",
                return_value=AccessDecision(AccessStatus.EXPIRED, None),
            ),
        ):
            self.assertIsNone(workout_ui.workout_entry_action(7))

    def test_changed_handler_modules_import_with_the_same_dispatcher(self) -> None:
        """The production import order has no handler-level circular import."""
        config = python_types.ModuleType("storage.config")
        config.dp = _Dispatcher()
        config.bot = object()
        config.admins = []
        config.bot_token = "test-token"
        config.DEBUG_MODE = False
        with patch.dict(sys.modules, {"storage.config": config}):
            for module_name in (
                "handlers.onboarding",
                "handlers.start",
                "handlers.workout_plan",
            ):
                sys.modules.pop(module_name, None)
                importlib.import_module(module_name)

    def test_start_during_active_workout_only_offers_resume(self) -> None:
        start_handler = _load_handler_module_with_fake_config("handlers.start")
        call = _Call()
        state = _State()
        resume_markup = workout_ui.start_mkp(
            ("▶️ Продолжить тренировку", "workout:resume"),
            show_history=True,
        )
        with (
            patch.object(start_handler.User, "get", return_value=_User()),
            patch.object(start_handler, "has_completed_profile", return_value=True),
            patch.object(start_handler, "workout_menu_markup", return_value=resume_markup),
        ):
            self.run_async(start_handler.startCall(call, state))
        self.assertIn(
            "workout:resume", self.callback_values(call.message.edits[-1][1])
        )

    def test_plan_preview_adds_start_action_without_starting_trial(self) -> None:
        plan_handler = _load_handler_module_with_fake_config("handlers.workout_plan")
        call = _Call()
        state = _State()
        action = ("🏋️ Начать первую тренировку бесплатно", "workout:start")
        assignment = python_types.SimpleNamespace(plan=object(), fallback_notes=())
        with (
            patch.object(plan_handler.User, "get", return_value=_User()),
            patch.object(plan_handler, "assign_workout_plan", return_value=assignment),
            patch.object(plan_handler, "get_fitness_profile", return_value=object()),
            patch.object(
                plan_handler,
                "get_access_decision",
                return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            ),
            patch.object(plan_handler, "format_workout_plan_preview", return_value="preview"),
            patch.object(plan_handler, "workout_entry_action", return_value=action) as entry,
        ):
            self.run_async(plan_handler.workout_plan_call(call, state))
        entry.assert_called_once_with(7)
        self.assertIn("workout:start", self.callback_values(call.message.edits[-1][1]))

    def test_weight_and_reps_parsing_accepts_comma_and_zero(self) -> None:
        self.assertEqual(12.5, workout_ui.parse_workout_weight(" 12,5 "))
        self.assertEqual(0, workout_ui.parse_workout_weight("0"))
        self.assertEqual(10, workout_ui.parse_workout_reps("10"))
        for value in ("", "-1", "nan", "x" * 33):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    workout_ui.parse_workout_weight(value)
        for value in ("", "0", "2.5", "x" * 10):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    workout_ui.parse_workout_reps(value)

    def test_start_renders_persisted_step_and_activates_only_via_service(self) -> None:
        call = _Call()
        state = _State()
        start = WorkoutStartResult(_workout(), created=True, trial_activated=True)
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_or_start_workout", return_value=start) as service_start,
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "get_current_step", return_value=_step()),
            patch.object(
                workout_ui,
                "get_progression_recommendation",
                return_value=_recommendation(ProgressionReason.NO_HISTORY),
            ),
        ):
            self.run_async(workout_ui.workout_start_or_resume(call, state))

        service_start.assert_called_once_with(7)
        self.assertIn("Подход: 1 из 2", call.message.edits[-1][0])
        self.assertGreaterEqual(state.clear_count, 1)

    def test_record_flow_keeps_only_weight_and_session_in_fsm(self) -> None:
        state = _State()
        call = _Call()
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_current_step", return_value=_step()),
        ):
            self.run_async(workout_ui.workout_record_set(call, state))
        self.assertEqual(21, state.data["workout_session_id"])
        self.assertNotIn("exercise_id", state.data)
        self.assertNotIn("set_number", state.data)
        self.assertEqual(
            ["workout:cancel"],
            self.callback_values(call.message.edits[-1][1]),
        )

        weight_message = _Message("12,5")
        self.run_async(workout_ui.workout_weight_input(weight_message, state))
        self.assertEqual(12.5, state.data["pending_weight"])
        self.assertEqual(workout_ui.WorkoutExecution.awaiting_reps, state.current_state)
        self.assertEqual(
            ["workout:cancel"],
            self.callback_values(weight_message.answers[-1][1]),
        )

        reps_message = _Message("10")
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_current_step", return_value=_step()),
            patch.object(workout_ui, "record_set_result") as save,
            patch.object(workout_ui, "show_current_workout", new_callable=AsyncMock),
        ):
            self.run_async(workout_ui.workout_reps_input(reps_message, state))
        save.assert_called_once_with(7, 21, 31, 1, 12.5, 10)
        self.assertEqual({}, state.data)
        self.assertIn("12.5 кг × 10", reps_message.answers[0][0])

    def test_invalid_input_reprompts_without_losing_fsm(self) -> None:
        state = _State()
        state.data = {"workout_session_id": 21}
        state.current_state = workout_ui.WorkoutExecution.awaiting_weight
        message = _Message("bad")
        self.run_async(workout_ui.workout_weight_input(message, state))
        self.assertEqual({"workout_session_id": 21}, state.data)
        self.assertEqual(workout_ui.WorkoutExecution.awaiting_weight, state.current_state)

        state.data["pending_weight"] = 0
        state.current_state = workout_ui.WorkoutExecution.awaiting_reps
        message = _Message("0")
        self.run_async(workout_ui.workout_reps_input(message, state))
        self.assertEqual(0, state.data["pending_weight"])
        self.assertEqual(workout_ui.WorkoutExecution.awaiting_reps, state.current_state)

    def test_resume_after_fsm_loss_uses_database_step(self) -> None:
        message = _Message()
        # No FSM data is supplied: the persisted session is the only cursor.
        with (
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "get_current_step", return_value=_step(set_number=2)) as current,
            patch.object(
                workout_ui,
                "get_progression_recommendation",
                return_value=_recommendation(ProgressionReason.NO_HISTORY),
            ),
        ):
            self.run_async(workout_ui.show_current_workout(message, 7, edit=False))
        current.assert_called_once_with(7, 21)
        self.assertIn("Подход: 2 из 2", message.answers[0][0])

    def test_progression_hint_formats_main_recommendation_actions(self) -> None:
        exercise = _exercise()
        cases = (
            (
                _recommendation(
                    ProgressionReason.INCREASE_WEIGHT,
                    suggested_weight="21",
                    suggested_reps=(8, 8, 8),
                    previous_weights=("20", "20", "20"),
                    previous_reps=(12, 12, 12),
                ),
                "Сегодня: попробуй 21 кг, цель 8/8/8.",
            ),
            (
                _recommendation(
                    ProgressionReason.HOLD_ADD_REPS,
                    suggested_weight="20",
                    suggested_reps=(12, 11, 9),
                    previous_weights=("20", "20", "20"),
                    previous_reps=(12, 10, 8),
                ),
                "Сегодня: оставь 20 кг и попробуй 12/11/9.",
            ),
            (
                _recommendation(
                    ProgressionReason.HOLD_RECOVER_RANGE,
                    suggested_weight="20",
                    suggested_reps=(10, 8, 8),
                    previous_weights=("20", "20", "20"),
                    previous_reps=(10, 8, 7),
                ),
                "Сегодня: оставь 20 кг, цель 10/8/8.",
            ),
            (
                _recommendation(
                    ProgressionReason.DECREASE_WEIGHT,
                    suggested_weight="19",
                    suggested_reps=(8, 8, 8),
                    previous_weights=("20", "20", "20"),
                    previous_reps=(7, 7, 6),
                ),
                "Сегодня: попробуй 19 кг, цель 8/8/8.",
            ),
        )
        for recommendation, expected_today in cases:
            with self.subTest(reason=recommendation.reason):
                text = workout_ui.format_progression_recommendation(exercise, recommendation)
                self.assertIn("Прошлый раз: 20 кг", text)
                self.assertIn(expected_today, text)

    def test_progression_hint_formats_neutral_mixed_and_bodyweight_states(self) -> None:
        exercise = _exercise()
        no_history = workout_ui.format_progression_recommendation(
            exercise,
            _recommendation(ProgressionReason.NO_HISTORY),
        )
        insufficient = workout_ui.format_progression_recommendation(
            exercise,
            _recommendation(ProgressionReason.INSUFFICIENT_DATA),
        )
        mixed = workout_ui.format_progression_recommendation(
            exercise,
            _recommendation(
                ProgressionReason.MIXED_WEIGHTS_HOLD,
                previous_weights=("20", "22.5", "20"),
                previous_reps=(12, 12, 12),
            ),
        )
        bodyweight = workout_ui.format_progression_recommendation(
            exercise,
            _recommendation(
                ProgressionReason.BODYWEIGHT_ADD_REPS,
                suggested_weight="0",
                suggested_reps=(12, 11, 9),
                previous_weights=("0", "0", "0"),
                previous_reps=(12, 10, 8),
            ),
        )

        self.assertIn("Выберите комфортный рабочий вес", no_history)
        self.assertIn("Данных для подсказки", insufficient)
        self.assertIn("20 кг/22,5 кг/20 кг", mixed)
        self.assertIn("не меняйте веса автоматически", mixed)
        self.assertIn("собств. вес", bodyweight)
        self.assertIn("попробуй 12/11/9", bodyweight)

    def test_progression_hint_is_read_only_and_stable_after_resume(self) -> None:
        message = _Message()
        recommendation = _recommendation(
            ProgressionReason.HOLD_ADD_REPS,
            suggested_weight="20",
            suggested_reps=(12, 11, 9),
            previous_weights=("20", "20", "20"),
            previous_reps=(12, 10, 8),
        )
        with (
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "get_current_step", return_value=_step(set_number=2)),
            patch.object(
                workout_ui,
                "get_progression_recommendation",
                return_value=recommendation,
            ) as progression_service,
            patch.object(workout_ui, "record_set_result") as record_set,
        ):
            self.run_async(workout_ui.show_current_workout(message, 7, edit=False))
            self.run_async(workout_ui.show_current_workout(message, 7, edit=False))

        self.assertEqual(message.answers[0][0], message.answers[1][0])
        self.assertEqual(2, progression_service.call_count)
        progression_service.assert_called_with(7, 31)
        record_set.assert_not_called()

    def test_cancel_requires_confirmation_and_preserves_service_owned_results(self) -> None:
        call = _Call()
        state = _State()
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
        ):
            self.run_async(workout_ui.workout_cancel_request(call, state))
        callbacks = self.callback_values(call.message.edits[-1][1])
        self.assertEqual(
            ["workout:cancel:confirm", "workout:cancel:resume"], callbacks
        )

        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "cancel_workout") as cancel,
            patch.object(workout_ui, "workout_menu_markup"),
        ):
            self.run_async(workout_ui.workout_cancel_confirm(call, state))
        cancel.assert_called_once_with(7, 21)
        self.assertGreaterEqual(state.clear_count, 1)

    def test_stale_and_foreign_callbacks_are_controlled(self) -> None:
        call = _Call()
        state = _State()
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_current_step", side_effect=WorkoutNotFoundError("gone")),
            patch.object(workout_ui, "record_set_result") as record,
            patch.object(workout_ui, "show_current_workout", new_callable=AsyncMock),
        ):
            self.run_async(workout_ui.workout_record_set(call, state))
        record.assert_not_called()
        self.assertGreaterEqual(state.clear_count, 1)

    def test_history_renders_completed_snapshot_summaries_and_empty_state(self) -> None:
        call = _Call()
        state = _State()
        empty_page = WorkoutHistoryPage((), 0, 5, False, False)
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_workout_history_page", return_value=empty_page),
        ):
            self.run_async(workout_ui.workout_history(call, state))
        self.assertIn("Завершённых тренировок пока нет", call.message.edits[-1][0])

        completed = _workout(
            status="completed",
            finished_at=datetime(2026, 8, 10, 13, 0),
        )
        page = WorkoutHistoryPage((completed,), 0, 5, False, False)
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(
                workout_ui,
                "get_workout_history_page",
                return_value=page,
            ) as history_page,
        ):
            self.run_async(workout_ui.workout_history(call, state))
        self.assertIn("День 1", call.message.edits[-1][0])
        self.assertIn("1 упражнений", call.message.edits[-1][0])
        history_page.assert_called_once_with(7, offset=0, page_size=5)

    def test_history_pagination_uses_only_requested_page_and_back_offset(self) -> None:
        first_page = WorkoutHistoryPage(
            tuple(
                _workout(
                    status="completed",
                    workout_id=index,
                    day_number=index,
                    finished_at=datetime(2026, 8, 10, 13, index),
                )
                for index in range(1, 6)
            ),
            0,
            5,
            False,
            True,
        )
        call = _Call()
        state = _State()
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_workout_history_page", return_value=first_page) as page_service,
        ):
            self.run_async(workout_ui.workout_history(call, state))
        page_service.assert_called_once_with(7, offset=0, page_size=5)
        callbacks = self.callback_values(call.message.edits[-1][1])
        self.assertIn("workout:history:page:5", callbacks)
        self.assertNotIn("workout:history:page:0", callbacks)
        self.assertEqual(5, sum(value.startswith("workout:history:detail:") for value in callbacks))

        older_page = WorkoutHistoryPage(
            (_workout(status="completed", workout_id=6, finished_at=datetime(2026, 8, 9, 13, 0)),),
            5,
            5,
            True,
            False,
        )
        older_call = _Call(data="workout:history:page:5")
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_workout_history_page", return_value=older_page) as page_service,
        ):
            self.run_async(workout_ui.workout_history_page(older_call, _State()))
        page_service.assert_called_once_with(7, offset=5, page_size=5)
        callbacks = self.callback_values(older_call.message.edits[-1][1])
        self.assertIn("workout:history:page:0", callbacks)
        self.assertNotIn("workout:history:page:10", callbacks)

    def test_history_detail_is_compact_snapshot_based_and_safe(self) -> None:
        results = (
            WorkoutSetResultView(1, 1, 12.5, 10, datetime(2026, 8, 10, 12, 1)),
            WorkoutSetResultView(2, 2, 12.5, 10, datetime(2026, 8, 10, 12, 2)),
            WorkoutSetResultView(3, 3, 0, 12, datetime(2026, 8, 10, 12, 3)),
        )
        workout = _workout(
            status="completed",
            finished_at=datetime(2026, 8, 10, 13, 0),
            exercises=(
                _exercise(name="Снимок жима", results=results),
                _exercise(exercise_id=32, order=2, name="Тяга", results=()),
            ),
        )
        call = _Call(data="workout:history:detail:21:5")
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_completed_workout_detail", return_value=workout),
        ):
            self.run_async(workout_ui.workout_history_detail(call, _State()))
        text = call.message.edits[-1][0]
        self.assertIn("Снимок жима — 12,5×10 · 12,5×10 · собств. вес×12", text)
        self.assertIn("Тяга — нет сохранённых подходов", text)
        self.assertIn("workout:history:page:5", self.callback_values(call.message.edits[-1][1]))

        stale_call = _Call(data="workout:history:detail:999:0")
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_completed_workout_detail", side_effect=WorkoutStateError("cancelled")),
        ):
            self.run_async(workout_ui.workout_history_detail(stale_call, _State()))
        self.assertTrue(stale_call.answers[-1][1]["show_alert"])

    def test_detail_messages_split_between_exercises(self) -> None:
        workout = _workout(
            status="completed",
            finished_at=datetime(2026, 8, 10, 13, 0),
            exercises=(
                _exercise(name="Первое упражнение"),
                _exercise(exercise_id=32, order=2, name="Второе упражнение"),
            ),
        )
        messages = workout_ui.format_workout_detail_messages(workout, message_limit=90)
        self.assertGreater(len(messages), 1)
        self.assertTrue(any("Первое упражнение" in message for message in messages))
        self.assertTrue(any("Второе упражнение" in message for message in messages))

    def test_complete_uses_active_session_and_stale_completion_does_not_start_new_one(self) -> None:
        call = _Call()
        state = _State()
        finished = _workout(status="completed")
        finished = finished.__class__(
            **{**finished.__dict__, "finished_at": datetime(2026, 8, 10, 13, 0)}
        )
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_active_workout", return_value=_workout()),
            patch.object(workout_ui, "complete_workout", return_value=finished) as complete,
            patch.object(workout_ui, "get_or_start_workout") as start,
        ):
            self.run_async(workout_ui.workout_complete(call, state))
        complete.assert_called_once_with(7, 21)
        start.assert_not_called()
        self.assertIn("Тренировка завершена", call.message.edits[-1][0])


if __name__ == "__main__":
    unittest.main()
