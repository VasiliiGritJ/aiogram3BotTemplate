import asyncio
from dataclasses import dataclass
from datetime import datetime
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import AsyncMock, patch

from services.access import AccessDecision, AccessStatus
from services.workout_execution import (
    CurrentWorkoutStep,
    WorkoutAccessDeniedError,
    WorkoutNotFoundError,
    WorkoutSessionExerciseView,
    WorkoutSessionView,
    WorkoutStartResult,
)


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
    def __init__(self, message: _Message | None = None, user_id: int = 101) -> None:
        self.message = message or _Message(user_id=user_id)
        self.from_user = _FromUser(user_id)
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


def _exercise(*, sets: int = 2) -> WorkoutSessionExerciseView:
    return WorkoutSessionExerciseView(
        id=31,
        exercise_order=1,
        planned_exercise_id=1,
        planned_exercise_name="Жим",
        planned_primary_muscle_group="Грудь",
        planned_target_sets=sets,
        planned_target_reps_min=8,
        planned_target_reps_max=12,
        planned_rest_seconds=90,
        planned_hint="Контролируйте движение",
        selected_exercise_id=1,
        selected_exercise_name="Жим",
        selected_primary_muscle_group="Грудь",
        selected_target_sets=sets,
        selected_target_reps_min=8,
        selected_target_reps_max=12,
        selected_rest_seconds=90,
        selected_hint="Контролируйте движение",
        set_results=(),
    )


def _workout(*, status: str = "in_progress") -> WorkoutSessionView:
    started = datetime(2026, 8, 10, 12, 0)
    return WorkoutSessionView(
        id=21,
        user_id=7,
        source_plan_id=1,
        source_plan_day_id=1,
        day_number=1,
        day_title="Верх тела",
        status=status,
        started_at=started,
        finished_at=None,
        updated_at=started,
        exercises=(_exercise(),),
    )


def _step(*, ready: bool = False, set_number: int = 1) -> CurrentWorkoutStep:
    return CurrentWorkoutStep(
        kind="ready_to_complete" if ready else "record_set",
        workout_id=21,
        exercise=None if ready else _exercise(),
        set_number=None if ready else set_number,
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

        weight_message = _Message("12,5")
        self.run_async(workout_ui.workout_weight_input(weight_message, state))
        self.assertEqual(12.5, state.data["pending_weight"])
        self.assertEqual(workout_ui.WorkoutExecution.awaiting_reps, state.current_state)

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
        ):
            self.run_async(workout_ui.show_current_workout(message, 7, edit=False))
        current.assert_called_once_with(7, 21)
        self.assertIn("Подход: 2 из 2", message.answers[0][0])

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
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_workout_history", return_value=()),
        ):
            self.run_async(workout_ui.workout_history(call, state))
        self.assertIn("пока пуста", call.message.edits[-1][0])

        completed = _workout(status="completed")
        completed = completed.__class__(
            **{**completed.__dict__, "finished_at": datetime(2026, 8, 10, 13, 0)}
        )
        with (
            patch.object(workout_ui.User, "get", return_value=_User()),
            patch.object(workout_ui, "get_workout_history", return_value=(completed,)),
        ):
            self.run_async(workout_ui.workout_history(call, state))
        self.assertIn("День 1", call.message.edits[-1][0])
        self.assertIn("Упражнений: 1", call.message.edits[-1][0])

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
