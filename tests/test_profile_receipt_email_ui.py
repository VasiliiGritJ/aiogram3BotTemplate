import asyncio
import importlib
import sys
import types as python_types
import unittest
from datetime import datetime
from unittest.mock import patch

from services.access import AccessDecision, AccessStatus
from services.receipt_email import ReceiptEmailValidationError


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler

    def message(self, *args, **kwargs):
        return lambda handler: handler


def _load_profile_module():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    config.bot = object()
    config.admins = []
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop("handlers.profile", None)
        return importlib.import_module("handlers.profile")


profile_ui = _load_profile_module()


class _State:
    def __init__(self) -> None:
        self.clear_count = 0
        self.states = []

    async def clear(self):
        self.clear_count += 1

    async def set_state(self, value):
        self.states.append(value)


class _Message:
    def __init__(self) -> None:
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class _Call:
    def __init__(self) -> None:
        self.from_user = python_types.SimpleNamespace(id=101, first_name="Тест")
        self.message = _Message()
        self.answered = 0

    async def answer(self):
        self.answered += 1


class _IncomingMessage(_Message):
    def __init__(self, text):
        super().__init__()
        self.text = text
        self.from_user = python_types.SimpleNamespace(id=101)


class ProfileReceiptEmailUiTests(unittest.TestCase):
    @staticmethod
    def run_async(coroutine):
        return asyncio.run(coroutine)

    @staticmethod
    def callbacks(markup):
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    def test_profile_actions_describe_add_or_change_and_keep_navigation(self):
        absent = profile_ui.profile_mpk(receipt_email_set=False)
        present = profile_ui.profile_mpk(receipt_email_set=True)
        self.assertEqual("Добавить email для чеков", absent.inline_keyboard[1][0].text)
        self.assertEqual("Изменить email для чеков", present.inline_keyboard[1][0].text)
        email_markup = profile_ui.receipt_email_mpk()
        self.assertEqual(["profile", "start"], self.callbacks(email_markup))

    def test_profile_screen_displays_the_stored_receipt_email(self):
        call = _Call()
        profile = python_types.SimpleNamespace(
            age=30,
            sex="male",
            height_cm=180,
            weight_kg=80.0,
            goal="muscle_gain",
            experience_level="beginner",
            training_environment="gym",
            workouts_per_week=3,
            session_duration_minutes=60,
            limitations=None,
        )
        user = python_types.SimpleNamespace(id=7)
        with (
            patch.object(profile_ui.User, "get", return_value=user),
            patch.object(profile_ui, "get_fitness_profile", return_value=profile),
            patch.object(profile_ui, "get_user_access", return_value=object()),
            patch.object(profile_ui, "evaluate_access", return_value=AccessDecision(AccessStatus.TRIAL, datetime(2026, 9, 1, 12, 0))),
            patch.object(profile_ui, "get_receipt_email", return_value="user@example.com"),
            patch.object(profile_ui, "create_start_link", return_value="https://example.invalid/start"),
        ):
            self.run_async(profile_ui.profileCall(call, _State()))
        self.assertIn("Email для чеков: user@example.com", call.message.edits[-1][0])
        self.assertEqual("Изменить email для чеков", call.message.edits[-1][1].inline_keyboard[1][0].text)

    def test_profile_email_edit_validates_and_preserves_back_navigation(self):
        call = _Call()
        state = _State()
        user = python_types.SimpleNamespace(id=7)
        with (
            patch.object(profile_ui.User, "get", return_value=user),
            patch.object(profile_ui, "get_fitness_profile", return_value=object()),
        ):
            self.run_async(profile_ui.profile_receipt_email_call(call, state))
        self.assertIn("Email для чеков", call.message.edits[-1][0])
        self.assertEqual(["profile", "start"], self.callbacks(call.message.edits[-1][1]))

        invalid = _IncomingMessage("not an email")
        with (
            patch.object(profile_ui.User, "get", return_value=user),
            patch.object(profile_ui, "save_receipt_email", side_effect=ReceiptEmailValidationError("bad")),
        ):
            self.run_async(profile_ui.profile_receipt_email_message(invalid, state))
        self.assertIn("Проверьте email", invalid.answers[-1][0])

        valid = _IncomingMessage("User@Example.com")
        with (
            patch.object(profile_ui.User, "get", return_value=user),
            patch.object(profile_ui, "save_receipt_email", return_value="user@example.com") as saved,
        ):
            self.run_async(profile_ui.profile_receipt_email_message(valid, state))
        saved.assert_called_once_with(7, "User@Example.com")
        self.assertIn("сохранён", valid.answers[-1][0])


if __name__ == "__main__":
    unittest.main()
