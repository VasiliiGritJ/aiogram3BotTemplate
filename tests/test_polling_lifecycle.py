import asyncio
from pathlib import Path
import tempfile
import unittest

from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates

from utils.polling_guard import PollingInstanceGuard
from utils.polling_lifecycle import (
    ConcurrentGetUpdatesBlocked,
    GetUpdatesOwner,
    PollingConflictDetected,
    PollingOwnerAlreadyRunning,
    StalePollingGenerationBlocked,
    run_polling_lifecycle,
)


class _MiddlewareManager:
    def __init__(self) -> None:
        self.items: list[object] = []

    def __call__(self, middleware) -> None:
        self.items.append(middleware)

    def unregister(self, middleware) -> None:
        self.items.remove(middleware)


class _Session:
    def __init__(self) -> None:
        self.middleware = _MiddlewareManager()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _Bot:
    def __init__(self) -> None:
        self.session = _Session()


class _Dispatcher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def start_polling(self, bot, *, close_bot_session: bool) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class GetUpdatesOwnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner = GetUpdatesOwner(run_uuid="test-run", dispatcher_id=10)
        self.owner.activate()
        self.bot = _Bot()

    def tearDown(self) -> None:
        self.owner.deactivate()

    async def test_sequential_requests_never_overlap(self) -> None:
        calls = 0

        async def make_request(bot, method):
            nonlocal calls
            calls += 1
            return []

        await self.owner(make_request, self.bot, GetUpdates(timeout=10))
        await self.owner(make_request, self.bot, GetUpdates(timeout=10))

        self.assertEqual(2, calls)
        self.assertEqual(1, self.owner.max_in_flight)

    async def test_concurrent_direct_get_updates_is_blocked(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def make_request(bot, method):
            entered.set()
            await release.wait()
            return []

        first = asyncio.create_task(
            self.owner(make_request, self.bot, GetUpdates(timeout=10))
        )
        await entered.wait()
        with self.assertRaises(ConcurrentGetUpdatesBlocked):
            await self.owner(make_request, self.bot, GetUpdates(timeout=10))
        release.set()
        await first

        self.assertEqual(1, self.owner.request_count)
        self.assertEqual(1, self.owner.max_in_flight)

    async def test_failed_request_finishes_before_next_generation_request(self) -> None:
        calls = 0

        async def make_request(bot, method):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("network failure")
            return []

        with self.assertRaises(RuntimeError):
            await self.owner(make_request, self.bot, GetUpdates(timeout=10))
        self.assertEqual(0, self.owner.in_flight)
        await self.owner(make_request, self.bot, GetUpdates(timeout=10))

        self.assertEqual(2, calls)
        self.assertEqual(1, self.owner.max_in_flight)

    async def test_stale_generation_cannot_retry(self) -> None:
        calls = 0

        async def make_request(bot, method):
            nonlocal calls
            calls += 1
            return []

        self.owner.deactivate()
        with self.assertRaises(StalePollingGenerationBlocked):
            await self.owner(make_request, self.bot, GetUpdates(timeout=10))
        self.assertEqual(0, calls)

    async def test_typed_telegram_conflict_stops_without_retry(self) -> None:
        calls = 0
        method = GetUpdates(timeout=10)

        async def make_request(bot, requested_method):
            nonlocal calls
            calls += 1
            raise TelegramConflictError(
                method=requested_method,
                message="terminated by another getUpdates request",
            )

        with self.assertRaises(PollingConflictDetected):
            await self.owner(make_request, self.bot, method)

        self.assertEqual(1, calls)
        self.assertEqual(1, self.owner.conflict_count)
        self.assertEqual(0, self.owner.in_flight)

    async def test_second_process_owner_is_blocked(self) -> None:
        second = GetUpdatesOwner(run_uuid="second-run", dispatcher_id=20)
        with self.assertRaises(PollingOwnerAlreadyRunning):
            second.activate()


class PollingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temp_dir.name) / "polling.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def guard_factory(self):
        return PollingInstanceGuard(self.lock_path)

    async def test_one_dispatcher_lifecycle_closes_session_and_releases_guard(self) -> None:
        dispatcher = _Dispatcher()
        bot = _Bot()

        metrics = await run_polling_lifecycle(
            dispatcher,
            bot,
            guard_factory=self.guard_factory,
        )

        self.assertEqual(1, dispatcher.calls)
        self.assertEqual(1, bot.session.close_calls)
        self.assertEqual([], bot.session.middleware.items)
        self.assertEqual(0, metrics.conflict_count)

        next_guard = PollingInstanceGuard(self.lock_path)
        next_guard.acquire()
        next_guard.release()

    async def test_exception_path_closes_session_and_releases_guard(self) -> None:
        dispatcher = _Dispatcher(error=RuntimeError("startup failed"))
        bot = _Bot()

        with self.assertRaises(RuntimeError):
            await run_polling_lifecycle(
                dispatcher,
                bot,
                guard_factory=self.guard_factory,
            )

        self.assertEqual(1, bot.session.close_calls)
        self.assertEqual([], bot.session.middleware.items)
        next_guard = PollingInstanceGuard(self.lock_path)
        next_guard.acquire()
        next_guard.release()


if __name__ == "__main__":
    unittest.main()
