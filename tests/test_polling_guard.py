import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from utils.polling_guard import PollingInstanceAlreadyRunning, PollingInstanceGuard


class PollingInstanceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._temp_dir.name) / "polling.lock"
        self._guards: list[PollingInstanceGuard] = []
        self._processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for guard in reversed(self._guards):
            guard.release()
        for process in self._processes:
            self._stop_process(process)
        self._temp_dir.cleanup()

    def test_first_instance_acquires_the_lock(self) -> None:
        guard = PollingInstanceGuard(self.lock_path)
        guard.acquire()
        self._guards.append(guard)
        self.assertTrue(self.lock_path.exists())

    def test_second_concurrent_process_is_rejected(self) -> None:
        holder_code = """
from pathlib import Path
import sys
import time
from utils.polling_guard import PollingInstanceGuard

with PollingInstanceGuard(Path(sys.argv[1])):
    print('LOCKED', flush=True)
    time.sleep(10)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", holder_code, str(self.lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._processes.append(process)
        self.assertEqual("LOCKED\n", process.stdout.readline())

        with self.assertRaises(PollingInstanceAlreadyRunning):
            PollingInstanceGuard(self.lock_path).acquire()

    def test_release_allows_a_new_instance(self) -> None:
        first = PollingInstanceGuard(self.lock_path)
        first.acquire()
        first.release()

        second = PollingInstanceGuard(self.lock_path)
        second.acquire()
        self._guards.append(second)

    def test_second_owner_in_same_process_is_rejected(self) -> None:
        first = PollingInstanceGuard(self.lock_path)
        first.acquire()
        self._guards.append(first)

        with self.assertRaises(PollingInstanceAlreadyRunning):
            PollingInstanceGuard(self.lock_path).acquire()

    def test_context_manager_releases_after_an_exception(self) -> None:
        with self.assertRaises(RuntimeError):
            with PollingInstanceGuard(self.lock_path):
                raise RuntimeError("test cleanup")

        next_guard = PollingInstanceGuard(self.lock_path)
        next_guard.acquire()
        self._guards.append(next_guard)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
