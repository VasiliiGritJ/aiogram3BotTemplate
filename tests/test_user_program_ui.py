import asyncio
from dataclasses import dataclass
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import AsyncMock, patch

from services.user_programs import AdaptationMode, DeterministicRussianProgramParser


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler

    def message(self, *args, **kwargs):
        return lambda handler: handler


def _load_module():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop("handlers.user_program", None)
        return importlib.import_module("handlers.user_program")


ui = _load_module()


@dataclass
class _FromUser:
    id: int = 100


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.from_user = _FromUser()
        self.answers = []
        self.edits = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class _Call:
    def __init__(self, data=""):
        self.data = data
        self.from_user = _FromUser()
        self.message = _Message()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _State:
    def __init__(self):
        self.data = {}
        self.current = None

    async def clear(self):
        self.data.clear()
        self.current = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.current = value

    async def get_data(self):
        return dict(self.data)


class UserProgramUiTests(unittest.TestCase):
    @staticmethod
    def run_async(coro):
        return asyncio.run(coro)

    def test_text_parse_only_creates_preview_not_plan(self):
        message = _Message("День 1\nЖим лежа 3x10")
        state = _State()
        state.data["user_program_mode"] = "strict"
        profile = python_types.SimpleNamespace(training_environment="gym")
        with (
            patch.object(ui.User, "get", return_value=python_types.SimpleNamespace(id=7)),
            patch.object(ui, "get_fitness_profile", return_value=profile),
            patch.object(ui, "assign_user_program") as assign,
        ):
            self.run_async(ui.user_program_text(message, state))
        assign.assert_not_called()
        self.assertIn("Я понял программу так", message.answers[-1][0])
        self.assertIn("user_program_draft", state.data)

    def test_confirm_saves_once_and_stale_confirm_is_blocked(self):
        draft = DeterministicRussianProgramParser().parse(
            "День 1\nЖим лежа 3x10", AdaptationMode.ADAPTIVE,
            training_environment="gym",
        ).draft
        state = _State()
        state.data["user_program_draft"] = draft.to_payload()
        call = _Call("user_program:confirm")
        assignment = python_types.SimpleNamespace(plan=object(), created=True)
        with (
            patch.object(ui.User, "get", return_value=python_types.SimpleNamespace(id=7)),
            patch.object(ui, "assign_user_program", return_value=assignment) as assign,
            patch.object(ui, "format_workout_plan", return_value="plan"),
            patch.object(ui, "workout_entry_action", return_value=None),
        ):
            self.run_async(ui.user_program_confirm(call, state))
            self.run_async(ui.user_program_confirm(call, state))
        assign.assert_called_once()
        self.assertIn("Программа сохранена", call.message.edits[-1][0])
        self.assertTrue(call.answers[-1][1].get("show_alert"))

    def test_retry_keeps_mode_but_discards_structured_draft(self):
        state = _State()
        state.data = {"user_program_mode": "replacements", "user_program_draft": {"x": 1}}
        call = _Call("user_program:retry")
        self.run_async(ui.user_program_retry(call, state))
        self.assertEqual({"user_program_mode": "replacements"}, state.data)


if __name__ == "__main__":
    unittest.main()
