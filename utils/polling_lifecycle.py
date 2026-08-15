"""Single-owner lifecycle for the project's Telegram long polling."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates, TelegramMethod

from utils.polling_guard import PollingInstanceGuard


logger = logging.getLogger("polling.lifecycle")
ResultT = TypeVar("ResultT")


class PollingLifecycleError(BaseException):
    """Escape aiogram's generic network retry loop for ownership failures."""


class PollingConflictDetected(PollingLifecycleError):
    """A real Telegram getUpdates 409 was identified by exception type."""


class ConcurrentGetUpdatesBlocked(PollingLifecycleError):
    """A second project-owned getUpdates call tried to overlap the first."""


class StalePollingGenerationBlocked(PollingLifecycleError):
    """An obsolete polling generation tried to issue another request."""


class PollingOwnerAlreadyRunning(RuntimeError):
    """Another owner is active inside this Python process."""


_OWNER_STATE_LOCK = threading.Lock()
_ACTIVE_OWNER: GetUpdatesOwner | None = None


@dataclass(frozen=True)
class PollingRunMetrics:
    run_uuid: str
    request_count: int
    max_in_flight: int
    conflict_count: int


class GetUpdatesOwner:
    """Guard and observe one sequential getUpdates generation."""

    def __init__(self, *, run_uuid: str, dispatcher_id: int) -> None:
        self.run_uuid = run_uuid
        self.dispatcher_id = dispatcher_id
        self.request_count = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.conflict_count = 0
        self._active = False
        self._state_lock = threading.Lock()

    def activate(self) -> None:
        global _ACTIVE_OWNER
        with _OWNER_STATE_LOCK:
            if _ACTIVE_OWNER is not None and _ACTIVE_OWNER is not self:
                raise PollingOwnerAlreadyRunning(
                    "Another polling owner is already active in this process."
                )
            _ACTIVE_OWNER = self
            self._active = True

    def deactivate(self) -> None:
        global _ACTIVE_OWNER
        with _OWNER_STATE_LOCK:
            self._active = False
            if _ACTIVE_OWNER is self:
                _ACTIVE_OWNER = None

    def metrics(self) -> PollingRunMetrics:
        return PollingRunMetrics(
            run_uuid=self.run_uuid,
            request_count=self.request_count,
            max_in_flight=self.max_in_flight,
            conflict_count=self.conflict_count,
        )

    async def __call__(
        self,
        make_request: Callable[
            [Bot, TelegramMethod[ResultT]], Awaitable[ResultT]
        ],
        bot: Bot,
        method: TelegramMethod[ResultT],
    ) -> ResultT:
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)

        with self._state_lock:
            if not self._active:
                raise StalePollingGenerationBlocked(
                    "Obsolete polling generation cannot issue getUpdates."
                )
            if self.in_flight:
                raise ConcurrentGetUpdatesBlocked(
                    "Concurrent project getUpdates call blocked before Telegram."
                )
            self.request_count += 1
            request_number = self.request_count
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

        started_at = time.monotonic()
        try:
            return await make_request(bot, method)
        except TelegramConflictError as error:
            self.conflict_count += 1
            logger.error(
                "TELEGRAM_GETUPDATES_CONFLICT run=%s pid=%d request=%d "
                "elapsed=%.3f; polling stops without retry",
                self.run_uuid,
                os.getpid(),
                request_number,
                time.monotonic() - started_at,
            )
            raise PollingConflictDetected(
                "Telegram rejected the sole getUpdates owner with HTTP 409."
            ) from error
        finally:
            with self._state_lock:
                self.in_flight -= 1


async def run_polling_lifecycle(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    companion_factories: Iterable[Callable[[], Awaitable[Any]]] = (),
    guard_factory: Callable[[], PollingInstanceGuard] = PollingInstanceGuard,
) -> PollingRunMetrics:
    """Run exactly one dispatcher owner and close every resource on exit."""

    run_uuid = str(uuid4())
    owner = GetUpdatesOwner(run_uuid=run_uuid, dispatcher_id=id(dispatcher))
    companion_tasks: list[asyncio.Task[Any]] = []

    with guard_factory():
        owner.activate()
        bot.session.middleware(owner)
        logger.warning(
            "POLLING_START run=%s pid=%d ppid=%d executable=%s "
            "dispatcher_id=%d bot_id=%d session_id=%d",
            run_uuid,
            os.getpid(),
            os.getppid(),
            sys.executable,
            id(dispatcher),
            id(bot),
            id(bot.session),
        )
        try:
            companion_tasks = [
                asyncio.create_task(factory(), name=f"polling-companion-{index}")
                for index, factory in enumerate(companion_factories, start=1)
            ]
            await dispatcher.start_polling(bot, close_bot_session=True)
        finally:
            for task in companion_tasks:
                task.cancel()
            if companion_tasks:
                await asyncio.gather(*companion_tasks, return_exceptions=True)
            owner.deactivate()
            bot.session.middleware.unregister(owner)
            await bot.session.close()
            metrics = owner.metrics()
            logger.warning(
                "POLLING_STOP run=%s pid=%d requests=%d max_in_flight=%d "
                "conflicts=%d session_closed=true guard_releasing=true",
                run_uuid,
                os.getpid(),
                metrics.request_count,
                metrics.max_in_flight,
                metrics.conflict_count,
            )

    return metrics
