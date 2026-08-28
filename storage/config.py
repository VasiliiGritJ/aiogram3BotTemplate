from dataclasses import dataclass
from environs import Env
from aiogram import Dispatcher, Router

from storage.telegram_session import create_telegram_bot

def get_value_from_env(variable) -> str:
    env = Env()
    env.read_env("storage/.env")
    return env(variable)


def get_optional_value_from_env(variable) -> str:
    env = Env()
    env.read_env("storage/.env")
    return env.str(variable, default="").strip()


bot_token = get_value_from_env("BOT_TOKEN")
telegram_proxy_url = get_optional_value_from_env("TELEGRAM_PROXY_URL")
bot = create_telegram_bot(bot_token, telegram_proxy_url)
dp = Dispatcher()
router = Router()


botUrl = "t.me/"                             # url бота, например t.me/somebot
admins = []                                  # список ID админов (например - [123456, 7891011, 121314])

systemdServiceName = "some_bot"              # имя сервиса systemd для перезапуска через админ панель, например some_bot

DEBUG_MODE = False                          # Режим отладки (True/False), при True - в логах будет больше информации для отладки
