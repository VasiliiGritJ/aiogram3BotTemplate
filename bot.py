import asyncio
import logging

from storage.config import dp, bot
from managers.reminds import remindsManager
import utils.custom_logger as cl
from utils.polling_guard import PollingInstanceAlreadyRunning
from utils.polling_lifecycle import PollingConflictDetected, run_polling_lifecycle

import admin_panel.admin.admin
import admin_panel.mailing.mailing

import handlers.onboarding
import handlers.start
import handlers.profile
import handlers.workout_plan
import handlers.user_program
import handlers.workout_execution
import handlers.subscription
import handlers.chat_join

async def bot_stopped():
    cl.log("Bot", "critical", f"Bot has been stopped")

async def bot_started():
    cl.log("Bot", "warning", f"Bot has been started")

async def main():
    try:
        dp.shutdown.register(bot_stopped)
        dp.startup.register(bot_started)
        await run_polling_lifecycle(
            dp,
            bot,
            companion_factories=(remindsManager,),
        )
    except PollingInstanceAlreadyRunning:
        cl.log("Bot", "warning", "Local polling instance is already running")
    except PollingConflictDetected:
        logging.getLogger("polling.lifecycle").error(
            "Polling stopped after a typed TelegramConflictError"
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
