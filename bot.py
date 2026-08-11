import asyncio

from storage.config import dp, bot
from managers.reminds import remindsManager
import utils.custom_logger as cl

import admin_panel.admin.admin
import admin_panel.mailing.mailing

import handlers.onboarding
import handlers.start
import handlers.profile
import handlers.workout_plan
import handlers.workout_execution
import handlers.subscription
import handlers.chat_join

async def bot_stopped():
    cl.log("Bot", "critical", f"Bot has been stopped")

async def bot_started():
    cl.log("Bot", "warning", f"Bot has been started")

async def main():
    tasks = [
        dp.start_polling(bot),
        remindsManager()
    ]
    dp.shutdown.register(bot_stopped)
    dp.startup.register(bot_started)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
