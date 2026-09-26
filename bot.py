"""Explicit Telegram bot bootstrap and polling entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import importlib
import logging

from aiogram import Bot, Dispatcher

import storage.config as bot_registry
from storage.payment_runtime import (
    PaymentProviderName,
    build_payment_runtime,
    configure_payment_runtime,
    read_payment_mode,
)
from storage.runtime_bootstrap import (
    DatabaseRuntimeState,
    initialize_database_runtime,
    validate_payment_environment,
)
from storage.receipt_runtime import build_receipt_runtime_config
from storage.runtime_config import (
    ApplicationEnvironment,
    RuntimeConfigurationError,
    SettingsReader,
    build_bot_role_config,
    local_development_settings,
    process_environment_settings,
)
import utils.custom_logger as cl
from utils.polling_guard import PollingInstanceAlreadyRunning
from utils.polling_lifecycle import PollingConflictDetected, run_polling_lifecycle


_HANDLER_MODULES = (
    "admin_panel.admin.admin",
    "admin_panel.mailing.mailing",
    "handlers.onboarding",
    "handlers.start",
    "handlers.profile",
    "handlers.workout_plan",
    "handlers.user_program",
    "handlers.workout_execution",
    "handlers.subscription",
    "handlers.chat_join",
)


@dataclass(frozen=True)
class BotApplicationRuntime:
    bot: Bot
    dispatcher: Dispatcher
    database: DatabaseRuntimeState
    environment: ApplicationEnvironment


def build_bot_application(
    settings: SettingsReader,
    *,
    expected_environment: ApplicationEnvironment,
) -> BotApplicationRuntime:
    """Validate all bot-role dependencies before any polling side effect."""
    config = build_bot_role_config(settings, expected_environment=expected_environment)
    payment_mode = read_payment_mode(settings)
    validate_payment_environment(config.environment, payment_mode)
    payment_runtime = build_payment_runtime(settings)
    receipt_config = build_receipt_runtime_config(
        settings,
        environment=config.environment,
        provider_adapter_available=(
            payment_runtime.provider is PaymentProviderName.PAYANYWAY
        ),
    )
    if (
        payment_runtime.provider is PaymentProviderName.PAYANYWAY
        and receipt_config.provider_code != PaymentProviderName.PAYANYWAY.value
    ):
        raise RuntimeConfigurationError(
            "Receipt provider does not match the payment provider."
        )
    database = initialize_database_runtime(config.database_path)
    configure_payment_runtime(payment_runtime)
    bot = bot_registry.configure_bot_role(config)
    cl.configure_logging(debug=config.debug_mode)
    for module_name in _HANDLER_MODULES:
        importlib.import_module(module_name)
    logging.getLogger(__name__).info(
        "runtime_ready role=bot environment=%s schema_version=%d sqlite_journal=wal",
        config.environment.value,
        database.validation.migration_versions[-1],
    )
    return BotApplicationRuntime(bot, bot_registry.dp, database, config.environment)


async def bot_stopped() -> None:
    cl.log("Bot", "warning", "Bot has been stopped")


async def bot_started() -> None:
    cl.log("Bot", "warning", "Bot has been started")


async def main(runtime: BotApplicationRuntime) -> None:
    from db.models import dbSession
    from managers.reminds import remindsManager

    try:
        runtime.dispatcher.shutdown.register(bot_stopped)
        runtime.dispatcher.startup.register(bot_started)
        await run_polling_lifecycle(
            runtime.dispatcher,
            runtime.bot,
            companion_factories=(remindsManager,),
        )
    except PollingInstanceAlreadyRunning:
        cl.log("Bot", "warning", "Local polling instance is already running")
    except PollingConflictDetected:
        logging.getLogger("polling.lifecycle").error(
            "Polling stopped after a typed TelegramConflictError"
        )
    finally:
        dbSession.dispose()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Telegram polling role.")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use process environment only and require production configuration.",
    )
    arguments = parser.parse_args(argv)
    if arguments.production:
        settings = process_environment_settings()
        environment = ApplicationEnvironment.PRODUCTION
    else:
        settings = local_development_settings()
        environment = ApplicationEnvironment.DEVELOPMENT
    runtime = build_bot_application(settings, expected_environment=environment)
    try:
        asyncio.run(main(runtime))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
