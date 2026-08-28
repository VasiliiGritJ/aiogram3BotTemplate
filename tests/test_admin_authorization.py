import asyncio
from dataclasses import dataclass
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import AsyncMock, patch


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler

    def message(self, *args, **kwargs):
        return lambda handler: handler


class _UserStore:
    @staticmethod
    def all():
        return []

    @staticmethod
    def get(**kwargs):
        del kwargs
        return None


def _load_admin_modules():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    config.admins = [7]
    config.systemdServiceName = "test-service"
    config.bot = python_types.SimpleNamespace(copy_message=AsyncMock())
    database = python_types.ModuleType("db")
    database.User = _UserStore
    module_names = (
        "admin_panel.authorization",
        "admin_panel.admin.markups",
        "admin_panel.admin.admin",
        "admin_panel.mailing.mailing",
    )
    with patch.dict(sys.modules, {"storage.config": config, "db": database}):
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        authorization = importlib.import_module("admin_panel.authorization")
        admin = importlib.import_module("admin_panel.admin.admin")
        mailing = importlib.import_module("admin_panel.mailing.mailing")
    return authorization, admin, mailing


authorization, admin_ui, mailing_ui = _load_admin_modules()


@dataclass
class _FromUser:
    id: int


class _Message:
    def __init__(self, user_id: int, text: str = "тест") -> None:
        self.from_user = _FromUser(user_id)
        self.text = text
        self.message_id = 1
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


class _Call:
    def __init__(self, data: str, user_id: int) -> None:
        self.data = data
        self.from_user = _FromUser(user_id)
        self.message = _Message(user_id)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs) -> None:
        self.answers.append((args, kwargs))


class _State:
    def __init__(self) -> None:
        self.clear_calls = 0
        self.current = None
        self.data: dict[str, object] = {}

    async def clear(self) -> None:
        self.clear_calls += 1
        self.current = None
        self.data.clear()

    async def set_state(self, state) -> None:
        self.current = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)


class AdminAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.admins_patch = patch.object(authorization, "admins", [7])
        self.admins_patch.start()
        self.addCleanup(self.admins_patch.stop)

    def run_async(self, awaitable) -> None:
        asyncio.run(awaitable)

    def assert_callback_denied(self, call: _Call, state: _State) -> None:
        self.assertEqual(1, state.clear_calls)
        self.assertEqual(
            ((authorization.DENIAL_TEXT,), {"show_alert": True}), call.answers[-1]
        )
        self.assertEqual([], call.message.edits)

    def test_authorization_accepts_only_explicit_positive_integer_config(self) -> None:
        self.assertTrue(authorization.is_authorized_admin(7, [7]))
        for invalid_config in ("7", {"7"}, [True], [0], [-7]):
            self.assertFalse(authorization.is_authorized_admin(7, invalid_config))
        self.assertFalse(authorization.is_authorized_admin(True, [True]))
        self.assertFalse(authorization.is_authorized_admin(8, [7]))

    def test_unauthorized_admin_callbacks_are_all_denied_before_data_or_restart(self) -> None:
        handlers = (
            (admin_ui.adminCall, "admin"),
            (admin_ui.adminUsersMenuCall, "all_users_1"),
            (admin_ui.adminUserMenuCall, "checkuser_100_1"),
            (admin_ui.restartBotCall, "restart_bot"),
            (mailing_ui.mailingCall, "admin_mailing"),
            (mailing_ui.mailingAllCall, "admin_mailing_all"),
            (mailing_ui.mailingUserCall, "admin_mailing_user"),
        )
        with (
            patch.object(admin_ui.User, "all") as all_users,
            patch.object(admin_ui.User, "get") as get_user,
            patch.object(admin_ui.os, "system") as restart,
        ):
            for handler, data in handlers:
                with self.subTest(data=data):
                    call = _Call(data, user_id=99)
                    state = _State()
                    self.run_async(handler(call, state))
                    self.assert_callback_denied(call, state)
            all_users.assert_not_called()
            get_user.assert_not_called()
            restart.assert_not_called()

    def test_unauthorized_admin_command_is_denied_without_showing_panel(self) -> None:
        message = _Message(user_id=99)
        state = _State()
        with patch.object(admin_ui.User, "all") as all_users:
            self.run_async(admin_ui.adminCmd(message, state))
        self.assertEqual(1, state.clear_calls)
        self.assertEqual([(authorization.DENIAL_TEXT, None)], message.answers)
        all_users.assert_not_called()

    def test_unauthorized_fsm_steps_cannot_send_or_continue_mailing(self) -> None:
        handlers = (
            mailing_ui.mailingAllGoCall,
            mailing_ui.mailingUserIdCall,
            mailing_ui.mailingUserGoCall,
        )
        with (
            patch.object(mailing_ui.User, "all") as all_users,
            patch.object(mailing_ui.bot, "copy_message") as copy_message,
        ):
            for handler in handlers:
                with self.subTest(handler=handler.__name__):
                    message = _Message(user_id=99)
                    state = _State()
                    state.data["tg_id"] = "100"
                    self.run_async(handler(message, state))
                    self.assertEqual(1, state.clear_calls)
                    self.assertEqual(
                        [(authorization.DENIAL_TEXT, None)], message.answers
                    )
            all_users.assert_not_called()
            copy_message.assert_not_called()

    def test_missing_admin_configuration_fails_closed(self) -> None:
        call = _Call("admin", user_id=7)
        state = _State()
        with (
            patch.object(authorization, "admins", None),
            patch.object(admin_ui.User, "all") as all_users,
        ):
            self.run_async(admin_ui.adminCall(call, state))
        self.assert_callback_denied(call, state)
        all_users.assert_not_called()

    def test_authorized_admin_panel_and_mailing_flow_remain_available(self) -> None:
        admin_call = _Call("admin", user_id=7)
        admin_state = _State()
        with patch.object(admin_ui.User, "all", return_value=[object(), object()]):
            self.run_async(admin_ui.adminCall(admin_call, admin_state))
        self.assertIn("Юзеров - 2", admin_call.message.edits[-1][0])

        mailing_call = _Call("admin_mailing_all", user_id=7)
        mailing_state = _State()
        self.run_async(mailing_ui.mailingAllCall(mailing_call, mailing_state))
        self.assertIs(mailing_ui.MailingAll.Msg, mailing_state.current)
        self.assertIn("Рассылка", mailing_call.message.edits[-1][0])

        message = _Message(user_id=7)
        with patch.object(mailing_ui.User, "all", return_value=[]):
            self.run_async(mailing_ui.mailingAllGoCall(message, mailing_state))
        self.assertIn("Рассылка завершена", message.answers[-1][0])
        self.assertEqual(1, mailing_state.clear_calls)


if __name__ == "__main__":
    unittest.main()
