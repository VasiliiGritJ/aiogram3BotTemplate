from dataclasses import dataclass
from environs import Env
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties

def get_value_from_env(variable) -> str:
    env = Env()
    env.read_env("storage/.env")
    return env(variable)

bot_token = get_value_from_env("BOT_TOKEN")
bot = Bot(bot_token, default=DefaultBotProperties(parse_mode='html'))
dp = Dispatcher()
router = Router()


botUrl = "t.me/"                             # url бота, например t.me/somebot
admins = []                                  # список ID админов (например - [123456, 7891011, 121314])

systemdServiceName = "some_bot"              # имя сервиса systemd для перезапуска через админ панель, например some_bot

DEBUG_MODE = False                          # Режим отладки (True/False), при True - в логах будет больше информации для отладки