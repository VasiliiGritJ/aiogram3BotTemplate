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

    def test_missing_proxy_uses_the_default_direct_session(self) -> None:
        bot = create_telegram_bot(TEST_TOKEN, None)
        try:
            self.assertIsInstance(bot.session, AiohttpSession)
            self.assertIsNone(bot.session._proxy)
        finally:
            self.close_bot(bot)

    def test_empty_proxy_value_is_disabled(self) -> None:
        self.assertIsNone(normalize_proxy_url(""))
        self.assertIsNone(normalize_proxy_url("   "))

        bot = create_telegram_bot(TEST_TOKEN, "   ")
        try:
            self.assertIsNone(bot.session._proxy)
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
        finally:
            self.close_bot(bot)

    def test_config_reads_optional_proxy_without_opening_real_env_file(self) -> None:
        values = {"BOT_TOKEN": TEST_TOKEN}
        read_paths: list[str] = []

        class _Env:
            def read_env(self, path: str) -> None:
                read_paths.append(path)

            def __call__(self, variable: str) -> str:
                return values[variable]

            def str(self, variable: str, default: str = "") -> str:
                return values.get(variable, default)

        fake_environs = python_types.ModuleType("environs")
        fake_environs.Env = _Env
        previous_config = sys.modules.pop("storage.config", None)
        try:
            with patch.dict(sys.modules, {"environs": fake_environs}):
                config = importlib.import_module("storage.config")
            self.assertEqual("", config.telegram_proxy_url)
            self.assertIsNone(config.bot.session._proxy)
            self.assertEqual(["storage/.env", "storage/.env"], read_paths)
            self.close_bot(config.bot)
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
            "admin_panel.admin.admin": python_types.ModuleType("admin_panel.admin.admin"),
            "admin_panel.mailing.mailing": python_types.ModuleType("admin_panel.mailing.mailing"),
            "handlers.onboarding": python_types.ModuleType("handlers.onboarding"),
            "handlers.start": python_types.ModuleType("handlers.start"),
            "handlers.profile": python_types.ModuleType("handlers.profile"),
            "handlers.workout_plan": python_types.ModuleType("handlers.workout_plan"),
            "handlers.workout_execution": python_types.ModuleType("handlers.workout_execution"),
            "handlers.chat_join": python_types.ModuleType("handlers.chat_join"),
        }
        with patch.dict(sys.modules, module_names):
            sys.modules.pop("bot", None)
            bot_module = importlib.import_module("bot")
            asyncio.run(bot_module.main())
            sys.modules.pop("bot", None)

        self.assertIn(config.bot, calls)
        self.assertIn("reminds", calls)


if __name__ == "__main__":
    unittest.main()
