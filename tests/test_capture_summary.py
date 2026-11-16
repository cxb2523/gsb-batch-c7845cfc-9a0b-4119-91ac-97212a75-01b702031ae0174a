# -*- coding: utf-8 -*-
"""
LinuxTask - test_capture_summary.py
Description: Unit tests for the per-recording capture summary: devices
             lost mid-recording are recorded (once) without killing the
             capture, and dedupe-set evictions are counted while recent
             events stay deduplicated. No display or hardware required:
             LinuxTaskApp is built via __new__ and fake devices stand in
             for evdev.InputDevice.
Author: JADRT22 (https://github.com/JADRT22)
License: MIT
"""

import threading
import time
import unittest
from collections import deque
from unittest.mock import MagicMock, patch

from evdev import ecodes as e
from evdev.events import InputEvent

import main
import recorder


def make_app():
    """Bare LinuxTaskApp with the state device_loop/toggle_record touch."""
    app = main.LinuxTaskApp.__new__(main.LinuxTaskApp)
    app.recording = False
    app.playing = False
    app.events = []
    app.events_lock = threading.Lock()
    app.start_time = 0.0
    app.start_cursor_pos = None
    app.hotkey_rec = e.KEY_F8
    app.hotkey_play = e.KEY_F9
    app.is_mapping = None
    app._rel_dx = 0
    app._rel_dy = 0
    app._rel_dirty = False
    app._processed_ids = set()
    app._processed_ids_lock = threading.Lock()
    app._processed_ids_order = deque()
    app._input_devices = []
    app._capture_lock = threading.Lock()
    app._capture_devices = []
    app._capture_disconnected = []
    app._dedupe_eviction_stats = {"passes": 0, "evicted": 0}
    app.capture_summary = None
    app._ui_queue = __import__('queue').Queue()
    # Skip the first-recording password dialog.
    app.app_config = {"hide_password_warning": True}
    app.uinput_device = None
    app.manager = MagicMock()
    app.manager.supports_absolute_positioning = True
    app.manager.get_cursor_pos.return_value = (100, 200)
    app.btn_rec = MagicMock()
    return app


def make_event(etype, code, value, sec=None, usec=0):
    if sec is None:
        sec = time.monotonic()
    return InputEvent(sec=sec, usec=usec, type=etype, code=code, value=value)


class FakeDevice:
    """Yields its events, like a healthy evdev.InputDevice."""

    def __init__(self, events, name="Fake Device"):
        self._events = list(events)
        self.name = name

    def read_loop(self):
        yield from self._events


class FailingDevice:
    """Yields some events, then dies with OSError (unplugged mid-recording)."""

    def __init__(self, events, name="Broken Device", error=None):
        self._events = list(events)
        self.name = name
        self._error = error or OSError(19, "No such device")

    def read_loop(self):
        yield from self._events
        raise self._error


class TestDeviceDisconnectSummary(unittest.TestCase):
    """A device dying mid-recording must be reported, once, while the
    remaining devices keep capturing."""

    def setUp(self):
        self.app = make_app()

    def test_disconnect_recorded_and_capture_continues(self):
        base = time.monotonic()
        failing = FailingDevice(
            [make_event(e.EV_KEY, e.KEY_A, 1, sec=base)],
            name="Broken Keyboard",
        )
        good = FakeDevice([
            make_event(e.EV_KEY, e.KEY_B, 1, sec=base + 0.01),
            make_event(e.EV_KEY, e.KEY_B, 0, sec=base + 0.02),
        ], name="Good Mouse")
        self.app._input_devices = [failing, good]

        self.app.toggle_record()
        self.assertTrue(self.app.recording)
        # The failing device records one event, then raises OSError; the
        # listener for it ends but nothing propagates.
        self.app.device_loop(failing)
        self.assertTrue(self.app.recording)
        # The healthy device keeps capturing afterwards.
        self.app.device_loop(good)
        self.app.toggle_record()  # stop -> builds the summary
        self.assertFalse(self.app.recording)

        summary = self.app.capture_summary
        self.assertIsNotNone(summary)
        self.assertEqual(summary["devices_total"], 2)
        self.assertEqual(
            summary["devices"], ["Broken Keyboard", "Good Mouse"]
        )
        self.assertEqual(len(summary["disconnected"]), 1)
        lost = summary["disconnected"][0]
        self.assertEqual(lost["name"], "Broken Keyboard")
        self.assertIn("No such device", lost["reason"])
        # Relative to recording start, like event timestamps.
        self.assertGreaterEqual(lost["time"], 0.0)
        # The event from the failing device plus both from the good one.
        self.assertEqual(summary["events_captured"], 3)
        with self.app.events_lock:
            codes = [ev["code"] for ev in self.app.events
                     if ev["type"] == "key"]
        self.assertEqual(codes, [e.KEY_A, e.KEY_B, e.KEY_B])

    def test_same_device_recorded_only_once(self):
        failing = FailingDevice(
            [make_event(e.EV_KEY, e.KEY_A, 1)], name="Flaky Device"
        )
        self.app._input_devices = [failing]
        self.app.toggle_record()
        self.app.device_loop(failing)
        self.app.device_loop(failing)  # dies again: must not duplicate
        self.app.toggle_record()
        disconnected = self.app.capture_summary["disconnected"]
        self.assertEqual(len(disconnected), 1)
        self.assertEqual(disconnected[0]["name"], "Flaky Device")

    def test_summary_reset_between_recordings(self):
        failing = FailingDevice([], name="Gone")
        self.app._input_devices = [failing]
        self.app.toggle_record()
        self.app.device_loop(failing)
        self.app.toggle_record()
        self.assertEqual(len(self.app.capture_summary["disconnected"]), 1)

        # Second recording with all devices healthy: clean summary.
        good = FakeDevice([make_event(e.EV_KEY, e.KEY_C, 1)], name="Fine")
        self.app._input_devices = [good]
        self.app.toggle_record()
        self.app.device_loop(good)
        self.app.toggle_record()
        summary = self.app.capture_summary
        self.assertEqual(summary["devices_total"], 1)
        self.assertEqual(summary["disconnected"], [])
        self.assertEqual(summary["events_captured"], 1)

    def test_bare_instance_degrades_without_summary_state(self):
        """__new__ instances without capture attrs must not raise."""
        app = main.LinuxTaskApp.__new__(main.LinuxTaskApp)
        app.recording = False
        app.events = []
        app.events_lock = threading.Lock()
        app.start_time = 0.0
        app.is_mapping = None
        app.hotkey_rec = e.KEY_F8
        app.hotkey_play = e.KEY_F9
        app._processed_ids = set()
        app._processed_ids_lock = threading.Lock()
        app._ui_queue = __import__('queue').Queue()
        # No _capture_lock/_capture_disconnected: log-only fallback.
        app.device_loop(FailingDevice([], name="Gone"))
        self.assertEqual(app.events, [])


class TestDedupeEvictionAccounting(unittest.TestCase):
    """Eviction must keep oldest-first semantics and be counted."""

    def setUp(self):
        self.app = make_app()

    def test_eviction_counted_and_recent_events_stay_deduped(self):
        base = time.monotonic()
        # Unique events, well past the patched cap.
        events = [
            make_event(e.EV_KEY, e.KEY_A, 1, sec=base, usec=i)
            for i in range(25)
        ]
        with patch.object(recorder, "MAX_DEDUPE_IDS", 10), \
                patch.object(recorder, "MAX_DEDUPE_EVICT", 4):
            self.app.toggle_record()
            self.app.device_loop(FakeDevice(events))
            stats = self.app._dedupe_eviction_stats
            self.assertGreaterEqual(stats["passes"], 1)
            self.assertGreater(stats["evicted"], 0)
            # The set was actually trimmed: oldest-first, bounded size.
            self.assertLessEqual(len(self.app._processed_ids), 10)
            # Recent events are still in the dedupe set: replaying the
            # newest one records nothing new.
            with self.app.events_lock:
                before = len(self.app.events)
            self.app.device_loop(FakeDevice([events[-1]]))
            with self.app.events_lock:
                after = len(self.app.events)
            self.assertEqual(before, after)
            self.assertEqual(after, 25)

        self.app.toggle_record()
        summary = self.app.capture_summary
        self.assertEqual(summary["dedupe_eviction_passes"], stats["passes"])
        self.assertEqual(summary["dedupe_evicted_ids"], stats["evicted"])
        self.assertEqual(summary["events_captured"], 25)

    def test_eviction_without_stats_structure_does_not_raise(self):
        """Bare __new__ instance: eviction works, counting is skipped."""
        app = main.LinuxTaskApp.__new__(main.LinuxTaskApp)
        app.recording = False
        app.events = []
        app.events_lock = threading.Lock()
        app.is_mapping = None
        app.hotkey_rec = e.KEY_F8
        app.hotkey_play = e.KEY_F9
        app._processed_ids = set()
        app._processed_ids_lock = threading.Lock()
        app._processed_ids_order = deque()
        app._ui_queue = __import__('queue').Queue()
        # No _dedupe_eviction_stats on purpose.
        base = time.monotonic()
        events = [
            make_event(e.EV_KEY, e.KEY_A, 1, sec=base, usec=i)
            for i in range(20)
        ]
        with patch.object(recorder, "MAX_DEDUPE_IDS", 5), \
                patch.object(recorder, "MAX_DEDUPE_EVICT", 2):
            app.device_loop(FakeDevice(events))  # must not raise
        self.assertLessEqual(len(app._processed_ids), 5)


if __name__ == "__main__":
    unittest.main()
