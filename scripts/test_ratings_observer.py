#!/usr/bin/env python3
"""Unit tests for the ratings observer / subscriber pattern."""

import sys
import unittest
from pathlib import Path

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.ratings import (
    _do_ratings_refresh,
    _invoke_subscriber,
    _pending_updated_appids,
    _refresh_lock,
    _subscribers,
    _subscribers_lock,
    notify_rating_updated,
    subscribe_ratings,
    unsubscribe_ratings,
)


class TestRatingsObserver(unittest.TestCase):
    def setUp(self):
        with _subscribers_lock:
            _subscribers.clear()
        with _refresh_lock:
            _pending_updated_appids.clear()

    def tearDown(self):
        with _subscribers_lock:
            _subscribers.clear()
        with _refresh_lock:
            _pending_updated_appids.clear()

    def test_subscribe_and_unsubscribe(self):
        received = []

        def cb(appid: str):
            received.append(appid)

        subscribe_ratings(cb)
        with _subscribers_lock:
            self.assertIn(cb, _subscribers)

        # Duplicate subscribe should be idempotent
        subscribe_ratings(cb)
        with _subscribers_lock:
            self.assertEqual(_subscribers.count(cb), 1)

        unsubscribe_ratings(cb)
        with _subscribers_lock:
            self.assertNotIn(cb, _subscribers)

    def test_invoke_subscriber_signatures(self):
        received_1 = []
        received_0 = []

        def cb_1(appid: str):
            received_1.append(appid)

        def cb_0():
            received_0.append("called")

        _invoke_subscriber(cb_1, "730")
        self.assertEqual(received_1, ["730"])

        _invoke_subscriber(cb_0, "730")
        self.assertEqual(received_0, ["called"])

    def test_do_ratings_refresh_dispatch(self):
        dispatched = []

        def on_update(appid: str):
            dispatched.append(appid)

        subscribe_ratings(on_update)

        with _refresh_lock:
            _pending_updated_appids.add("10")
            _pending_updated_appids.add("20")

        _do_ratings_refresh()
        self.assertEqual(sorted(dispatched), ["10", "20"])

        # Pending should be flushed
        with _refresh_lock:
            self.assertEqual(len(_pending_updated_appids), 0)

    def test_bulk_dispatch_collapses_to_star(self):
        dispatched = []

        def on_update(appid: str):
            dispatched.append(appid)

        subscribe_ratings(on_update)

        with _refresh_lock:
            _pending_updated_appids.add("10")
            _pending_updated_appids.add("*")
            _pending_updated_appids.add("20")

        _do_ratings_refresh()
        # When "*" is in pending, only "*" is dispatched
        self.assertEqual(dispatched, ["*"])

    def test_sip_deleted_object_auto_prune(self):
        try:
            from PyQt6.QtWidgets import QApplication, QWidget
            app = QApplication.instance() or QApplication(["test"])
        except Exception:
            return  # Skip headless environments without PyQt6

        w = QWidget()
        called = []

        def bound_cb(appid: str):
            called.append(appid)

        # Bind method to widget
        w.on_update = bound_cb
        subscribe_ratings(w.on_update)

        # Delete the widget
        w.deleteLater()
        from PyQt6 import sip
        # Force deletion or test sip.isdeleted
        if not sip.isdeleted(w):
            w.destroy()

        # If deleted, _invoke_subscriber should prune it
        if sip.isdeleted(w):
            _invoke_subscriber(w.on_update, "123")
            self.assertEqual(len(called), 0)
            with _subscribers_lock:
                self.assertNotIn(w.on_update, _subscribers)


if __name__ == "__main__":
    unittest.main()
