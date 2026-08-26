import concurrent.futures
import threading
import time
import unittest
from unittest import mock

from unshuffle.core.concurrency import bounded_map, max_scan_workers


class ConcurrencyTests(unittest.TestCase):
    def test_max_scan_workers_uses_same_cpu_aware_cap_on_macos(self):
        with mock.patch("unshuffle.core.concurrency.sys.platform", "darwin"), \
             mock.patch("os.cpu_count", return_value=16), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(max_scan_workers(20), 8)
            self.assertEqual(max_scan_workers(3), 3)

    def test_max_scan_workers_honors_global_env_override(self):
        with mock.patch.dict("os.environ", {"UNSHUFFLE_MAX_SCAN_WORKERS": "2"}, clear=True), \
             mock.patch("unshuffle.core.concurrency.sys.platform", "linux"), \
             mock.patch("os.cpu_count", return_value=64):
            self.assertEqual(max_scan_workers(20), 2)
            self.assertEqual(max_scan_workers(1), 1)

    def test_max_scan_workers_clamps_to_total_and_ignores_invalid_override(self):
        with mock.patch.dict("os.environ", {"UNSHUFFLE_MAX_SCAN_WORKERS": "invalid"}, clear=True), \
             mock.patch("unshuffle.core.concurrency.sys.platform", "linux"), \
             mock.patch("os.cpu_count", return_value=64):
            self.assertEqual(max_scan_workers(3), 3)
            self.assertEqual(max_scan_workers(0), 1)

    def test_bounded_map_limits_pending_futures(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def work(value: int) -> int:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return value * 2

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(bounded_map(executor, work, range(20), max_pending=3))

        self.assertLessEqual(max_active, 3)
        self.assertEqual(sorted(results), [(value, value * 2) for value in range(20)])

    def test_bounded_map_cancels_remaining_futures_on_break(self):
        started_events = {0: threading.Event(), 1: threading.Event()}
        proceed_events = {0: threading.Event(), 1: threading.Event()}
        futures = []

        def work(value: int) -> int:
            if value in started_events:
                started_events[value].set()
                proceed_events[value].wait()
            return value

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            original_submit = executor.submit
            def spy_submit(*args, **kwargs):
                fut = original_submit(*args, **kwargs)
                futures.append(fut)
                return fut
            executor.submit = spy_submit

            results = []
            def consume():
                gen = bounded_map(executor, work, range(5), max_pending=3)
                try:
                    for item, res in gen:
                        results.append(res)
                        break
                finally:
                    gen.close()
            
            t = threading.Thread(target=consume)
            t.start()

            # Wait for task 0 to start
            started_events[0].wait(timeout=2.0)
            
            # Let task 0 finish. This will free the worker thread to start task 1.
            proceed_events[0].set()

            # Wait for task 1 to start (so we know task 0 has finished and gen has yielded/broken)
            started_events[1].wait(timeout=2.0)

            # Wait for the consumer thread to finish
            t.join(timeout=2.0)

            # Let task 1 finish so the executor can shutdown cleanly
            proceed_events[1].set()

        self.assertEqual(results, [0])
        self.assertEqual(len(futures), 3)
        self.assertTrue(futures[0].done() and not futures[0].cancelled())
        self.assertTrue(futures[2].cancelled())

