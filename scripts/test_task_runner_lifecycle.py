#!/usr/bin/env python3
"""Test to ensure TaskRunner and QThread lifecycle never aborts on rapid cancellation/dereference."""

import sys
import time
import unittest
from pathlib import Path

# Ensure src/ is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QCoreApplication

# Ensure QCoreApplication exists
_app = QCoreApplication.instance() or QCoreApplication([])

from utils.task_runner import TaskRunner


class TestTaskRunnerLifecycle(unittest.TestCase):
    def test_01_task_runner_rapid_cancel_and_dereference_no_abort(self):
        """TaskRunner deleted while thread is running does not call qFatal / abort."""
        def slow_worker():
            time.sleep(0.3)
            return "done"

        runner = TaskRunner()
        runner.run(slow_worker)
        self.assertTrue(runner.is_running())

        # Rapid stop without waiting
        runner.stop(wait_ms=0, terminate_on_timeout=False)

        # Dereference runner while thread is still active in background
        del runner

        # Process Qt events to flush any pending deleteLater / deferred deletions
        _app.processEvents()

        # Sleep enough for background thread to naturally finish and clean up
        time.sleep(0.4)
        _app.processEvents()

    def test_02_task_runner_reuse(self):
        """TaskRunner instance can be reused across multiple successive runs cleanly."""
        results = []

        def worker_1():
            return 42

        def worker_2():
            return 84

        runner = TaskRunner()
        w1 = runner.run(worker_1, on_finished=lambda val: results.append(val))
        for _ in range(50):
            if results == [42]:
                break
            time.sleep(0.02)
            _app.processEvents()

        w2 = runner.run(worker_2, on_finished=lambda val: results.append(val))
        for _ in range(50):
            if results == [42, 84]:
                break
            time.sleep(0.02)
            _app.processEvents()

        self.assertEqual(results, [42, 84])


if __name__ == "__main__":
    unittest.main()
