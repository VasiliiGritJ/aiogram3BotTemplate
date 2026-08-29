"""Mutable process registry configured only by an explicit bot bootstrap."""

from aiogram import Bot, Dispatcher, Router

from storage.runtime_config import BotRoleConfig, RuntimeConfigurationError
from storage.telegram_session import create_telegram_bot


dp = Dispatcher()
router = Router()
bot: Bot | None = None
admins: list[int] = []
systemdServiceName = ""
DEBUG_MODE = False
botUrl = "t.me/"
telegram_proxy_url: str | None = None


def configure_bot_role(config: BotRoleConfig) -> Bot:
    global bot, systemdServiceName, DEBUG_MODE, telegram_proxy_url
    if bot is not None:
        raise RuntimeConfigurationError("Bot role is already configured.")
    configured_bot = create_telegram_bot(config.bot_token, config.telegram_proxy_url)
    bot = configured_bot
    admins[:] = config.admin_ids
    systemdServiceName = config.systemd_service_name
    DEBUG_MODE = config.debug_mode
    telegram_proxy_url = config.telegram_proxy_url
    return configured_bot
