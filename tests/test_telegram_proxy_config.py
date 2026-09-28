import asyncio
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import patch

from aiogram.client.session.aiohttp import AiohttpSession

from storage.telegram_session import create_telegram_bot, normalize_proxy_url


TEST_TOKEN = "123456:abcdefghijklmnopqrstuvwxyzABCDE"


class TelegramProxyConfigTests(unittest.TestCase):
    def close_bot(self, bot) -> None:
        asyncio.run(bot.session.close())

    def test_missing_proxy_uses_a_fresh_connection_direct_session(self) -> None:
        bot = create_telegram_bot(TEST_TOKEN, None)
        try:
            self.assertIsInstance(bot.session, AiohttpSession)
            self.assertIsNone(bot.session._proxy)
            self.assertTrue(bot.session._connector_init["force_close"])
        finally:
            self.close_bot(bot)

    def test_empty_proxy_value_is_disabled(self) -> None:
        self.assertIsNone(normalize_proxy_url(""))
        self.assertIsNone(normalize_proxy_url("   "))

        bot = create_telegram_bot(TEST_TOKEN, "   ")
        try:
            self.assertIsNone(bot.session._proxy)
            self.assertTrue(bot.session._connector_init["force_close"])
        finally:
            self.close_bot(bot)

    def test_socks_proxy_uses_aiogram_session_with_remote_dns(self) -> None:
        proxy_url = "socks5://proxy.example.invalid:1080"
        with self.assertNoLogs():
            bot = create_telegram_bot(TEST_TOKEN, proxy_url)
        try:
            self.assertEqual(proxy_url, bot.session._proxy)
            self.assertEqual("aiohttp_socks.connector", bot.session._connector_type.__module__)
            self.assertTrue(bot.session._connector_init["rdns"])
            self.assertNotIn("force_close", bot.session._connector_init)
        finally:
            self.close_bot(bot)

    def test_config_import_has_no_dotenv_or_bot_creation_side_effect(self) -> None:
        previous_config = sys.modules.pop("storage.config", None)
        try:
            with patch("environs.Env.read_env") as read_env:
                config = importlib.import_module("storage.config")
            read_env.assert_not_called()
            self.assertIsNone(config.telegram_proxy_url)
            self.assertIsNone(config.bot)
        finally:
            sys.modules.pop("storage.config", None)
            if previous_config is not None:
                sys.modules["storage.config"] = previous_config

    def test_bot_main_keeps_using_configured_bot_for_polling(self) -> None:
        calls: list[object] = []

        class _Registry:
            def register(self, callback) -> None:
                calls.append(callback)

        class _Dispatcher:
            def __init__(self) -> None:
                self.shutdown = _Registry()
                self.startup = _Registry()

            async def start_polling(self, bot) -> None:
                calls.append(bot)

        async def reminds_manager() -> None:
            calls.append("reminds")

        guard = python_types.ModuleType("utils.polling_guard")
        guard.PollingInstanceAlreadyRunning = RuntimeError

        lifecycle = python_types.ModuleType("utils.polling_lifecycle")
        lifecycle.PollingConflictDetected = RuntimeError

        async def run_polling_lifecycle(
            dispatcher,
            bot,
            *,
            companion_factories,
        ) -> None:
            calls.append("guard entered")
            await dispatcher.start_polling(bot)
            for factory in companion_factories:
                await factory()
            calls.append("guard released")

        lifecycle.run_polling_lifecycle = run_polling_lifecycle

        config = python_types.ModuleType("storage.config")
        config.dp = _Dispatcher()
        config.bot = object()
        reminds = python_types.ModuleType("managers.reminds")
        reminds.remindsManager = reminds_manager
        logger = python_types.ModuleType("utils.custom_logger")
        logger.log = lambda *args, **kwargs: None
        module_names = {
            "storage.config": config,
            "managers.reminds": reminds,
            "utils.custom_logger": logger,
            "utils.polling_guard": guard,
            "utils.polling_lifecycle": lifecycle,
            "admin_panel.admin.admin": python_types.ModuleType("admin_panel.admin.admin"),
            "admin_panel.mailing.mailing": python_types.ModuleType("admin_panel.mailing.mailing"),
            "handlers.onboarding": python_types.ModuleType("handlers.onboarding"),
            "handlers.start": python_types.ModuleType("handlers.start"),
            "handlers.profile": python_types.ModuleType("handlers.profile"),
            "handlers.workout_plan": python_types.ModuleType("handlers.workout_plan"),
            "handlers.user_program": python_types.ModuleType("handlers.user_program"),
            "handlers.workout_execution": python_types.ModuleType("handlers.workout_execution"),
            "handlers.subscription": python_types.ModuleType("handlers.subscription"),
            "handlers.chat_join": python_types.ModuleType("handlers.chat_join"),
        }
        with patch.dict(sys.modules, module_names):
            sys.modules.pop("bot", None)
            bot_module = importlib.import_module("bot")
            runtime = bot_module.BotApplicationRuntime(
                config.bot,
                config.dp,
                object(),
                bot_module.ApplicationEnvironment.DEVELOPMENT,
            )
            with patch("db.models.dbSession.dispose"):
                asyncio.run(bot_module.main(runtime))
            sys.modules.pop("bot", None)

        self.assertIn(config.bot, calls)
        self.assertIn("reminds", calls)
        self.assertIn("guard entered", calls)
        self.assertEqual("guard released", calls[-1])
        self.assertLess(calls.index(config.bot), calls.index("guard released"))
        self.assertLess(calls.index("reminds"), calls.index("guard released"))


if __name__ == "__main__":
    unittest.main()
