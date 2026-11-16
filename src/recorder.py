# -*- coding: utf-8 -*-
"""
LinuxTask - recorder.py
Description: Input capture half of the app (device enumeration, listener
             threads, event deduplication and timeline building), moved
             out of main.py. Methods are mixed into LinuxTaskApp, so the
             public surface is unchanged.
Author: JADRT22 (https://github.com/JADRT22)
License: MIT
"""

import logging
import threading
import time

import evdev
from evdev import ecodes as e

logger = logging.getLogger("LinuxTask")

# Cap for the device_loop dedupe set: bounds memory in long sessions.
# When exceeded, only the oldest entries are evicted (see device_loop)
# instead of clearing the whole set, which would reopen double-fire.
MAX_DEDUPE_IDS = 100000
MAX_DEDUPE_EVICT = 10000

# Name of our own virtual replay device (see init_uinput). Listeners skip
# any device with this name so replayed events are not read back (echo).
VIRTUAL_DEVICE_NAME = "LinuxTask-Virtual"


class Recorder:
    """Capture methods; state (events, locks, flags) lives on the app."""

    def get_input_devices(self):
        """Returns list of accessible input devices."""
        # NOTE: python-evdev >= 2.0 only lists readable+WRITABLE devices
        # by default, but our udev rule grants event* READ-ONLY on purpose
        # (recording only listens). Ask for readable devices explicitly;
        # fall back to the old no-arg call on evdev 1.x.
        try:
            paths = evdev.list_devices(writable=False)
        except TypeError:
            paths = evdev.list_devices()
        # NOTE: __dict__ lookup (not getattr): same reason as in
        # device_loop — on instances built via __new__ (unit tests, no
        # display) getattr() on a missing attr raises RecursionError
        # instead of returning the default. Verified empirically.
        own = self.__dict__.get("uinput_device")
        own_name = None
        if own is not None:
            try:
                own_name = own.name
            except (OSError, AttributeError):
                own_name = None
        devices = []
        for path in paths:
            try:
                dev = evdev.InputDevice(path)
            except (PermissionError, OSError) as exc:
                logger.debug("Cannot open %s: %s", path, exc)
                continue
            # Skip our own virtual replay device: playback writes key and
            # mouse events through it, and without this filter the listener
            # threads would read those replayed events back (echo),
            # re-triggering the F8/F9 hotkeys mid-playback. The match is by
            # device NAME, so a second app instance would also ignore the
            # first one's virtual device — acceptable, since each instance
            # only replays through its own UInput.
            try:
                dev_name = dev.name
            except (OSError, AttributeError):
                dev_name = None
            if (own_name is not None and dev_name is not None
                    and dev_name == own_name == VIRTUAL_DEVICE_NAME):
                logger.debug(
                    "Skipping own virtual device '%s' (%s).", dev_name, path
                )
                continue
            devices.append(dev)
        return devices

    def global_hardware_listener(self):
        """Spawns a listener thread for each input device."""
        devices = self.get_input_devices()
        if not devices:
            logger.warning(
                "No input devices accessible. "
                "Global hotkeys and recording will not work. "
                "Fix: re-run ./tools/install.sh, check membership in "
                "the 'input' group (groups $USER), then log out and back in."
            )
            self._ui_queue.put("no_devices")
            return
        self._input_devices = devices
        logger.info("Listening on %d input devices.", len(devices))
        for d in devices:
            threading.Thread(
                target=self.device_loop, args=(d,), daemon=True
            ).start()

    @staticmethod
    def _safe_device_name(dev):
        """Device name or None; a dying device may raise on .name."""
        try:
            return dev.name
        except (OSError, AttributeError):
            return None

    def _note_device_failure(self, dev, reason):
        """Records a device loss for the capture summary, once per device.

        Only the first failure per device name is kept (a flapping device
        must not flood the list). Degrades to log-only on bare instances
        built via __new__ in tests, where the summary state is absent.
        """
        name = self._safe_device_name(dev) or "<unknown device>"
        lock = self.__dict__.get("_capture_lock")
        disconnected = self.__dict__.get("_capture_disconnected")
        if lock is None or disconnected is None:
            return
        with lock:
            if any(d["name"] == name for d in disconnected):
                return
            disconnected.append({
                "name": name,
                "reason": reason,
                # Relative to recording start, like event timestamps.
                "time": time.monotonic() - self.start_time,
            })

    def get_capture_summary(self):
        """Returns a dict describing the current/last recording session:
        participating devices, devices lost mid-recording, dedupe
        eviction counters and the number of captured events. All lookups
        go through __dict__ so bare __new__ test instances get an empty
        summary instead of a RecursionError from tkinter's __getattr__.
        """
        lock = self.__dict__.get("_capture_lock")

        def _disconnected_copy():
            return [
                dict(d)
                for d in (self.__dict__.get("_capture_disconnected") or [])
            ]

        if lock is not None:
            with lock:
                disconnected = _disconnected_copy()
        else:
            disconnected = _disconnected_copy()
        stats = self.__dict__.get("_dedupe_eviction_stats") or {}
        devices = list(self.__dict__.get("_capture_devices") or [])
        events_lock = self.__dict__.get("events_lock")
        if events_lock is not None:
            with events_lock:
                events_captured = len(self.events)
        else:
            events_captured = len(self.__dict__.get("events") or [])
        return {
            "devices_total": len(devices),
            "devices": devices,
            "disconnected": disconnected,
            "dedupe_eviction_passes": stats.get("passes", 0),
            "dedupe_evicted_ids": stats.get("evicted", 0),
            "events_captured": events_captured,
        }

    def log_capture_summary(self):
        """Logs the capture summary so users can judge whether the
        recorded macro is complete. Returns the summary dict."""
        summary = self.get_capture_summary()
        logger.info(
            "Capture summary: %d device(s) listened, %d event(s) captured, "
            "dedupe evictions: %d pass(es) / %d id(s).",
            summary["devices_total"], summary["events_captured"],
            summary["dedupe_eviction_passes"],
            summary["dedupe_evicted_ids"],
        )
        if summary["disconnected"]:
            for d in summary["disconnected"]:
                logger.warning(
                    "Device '%s' was lost %.3fs into the recording: %s",
                    d["name"], d["time"], d["reason"],
                )
            logger.warning(
                "%d of %d device(s) dropped out during recording; "
                "the macro may be incomplete.",
                len(summary["disconnected"]), summary["devices_total"],
            )
        else:
            logger.info("All devices stayed connected for the whole recording.")
        return summary

    def _event_id(self, event):
        return (event.sec, event.usec, event.type, event.code, event.value)

    def device_loop(self, dev):
        """Main event reading loop for a single input device."""
        try:
            for event in dev.read_loop():
                # Deduplicate events across multiple devices
                eid = self._event_id(event)
                with self._processed_ids_lock:
                    if eid in self._processed_ids:
                        continue
                    self._processed_ids.add(eid)
                    # NOTE: __dict__ lookup (not getattr): this class is a
                    # tkinter widget whose __getattr__ recurses on missing
                    # attrs for instances built via __new__ in tests.
                    order = self.__dict__.get("_processed_ids_order")
                    if order is not None:
                        order.append(eid)
                    # Cap set size to prevent memory leak during long sessions.
                    # Evict only the oldest IDs so recent events stay
                    # deduplicated (a full clear would reopen double-fire).
                    if len(self._processed_ids) > MAX_DEDUPE_IDS:
                        if order is not None:
                            evicted = 0
                            for _ in range(min(len(order), MAX_DEDUPE_EVICT)):
                                oldest = order.popleft()
                                if oldest in self._processed_ids:
                                    self._processed_ids.discard(oldest)
                                    evicted += 1
                                if len(self._processed_ids) <= MAX_DEDUPE_IDS - MAX_DEDUPE_EVICT:
                                    break
                            # Leave a trace in the capture summary: evicted
                            # IDs may re-record if the same event reappears.
                            # __dict__ lookup: bare __new__ test instances
                            # have no stats dict — degrade to plain eviction.
                            stats = self.__dict__.get("_dedupe_eviction_stats")
                            if stats is not None:
                                stats["passes"] += 1
                                stats["evicted"] += evicted
                        else:
                            self._processed_ids.clear()

                # --- Mouse movement: record absolute or relative ---
                if event.type == e.EV_REL and self.recording:
                    if event.code == e.REL_WHEEL:
                        # monotonic: immune to NTP adjustments/DST jumps.
                        now = time.monotonic() - self.start_time
                        direction = 'up' if event.value > 0 else 'down'
                        with self.events_lock:
                            self.events.append({
                                "type": "scroll",
                                "direction": direction,
                                "clicks": abs(event.value),
                                "time": now
                            })
                        continue
                    if event.code == e.REL_X:
                        with self.events_lock:
                            self._rel_dx += event.value
                            self._rel_dirty = True
                    elif event.code == e.REL_Y:
                        with self.events_lock:
                            self._rel_dy += event.value
                            self._rel_dirty = True
                    else:
                        with self.events_lock:
                            self._rel_dirty = True

                if event.type == e.EV_SYN and self.recording:
                    with self.events_lock:
                        rel_dx, rel_dy = self._rel_dx, self._rel_dy
                        rel_dirty = self._rel_dirty
                        self._rel_dirty = False
                        self._rel_dx = 0
                        self._rel_dy = 0
                    if rel_dirty:
                        now = time.monotonic() - self.start_time
                        if self.manager.supports_absolute_positioning:
                            pos = self.manager.get_cursor_pos()
                            if pos is None:
                                logger.debug("EV_SYN flush skipped: cursor pos unknown")
                            else:
                                with self.events_lock:
                                    self.events.append({
                                        "type": "pos", "x": pos[0],
                                        "y": pos[1], "time": now
                                    })
                        else:
                            with self.events_lock:
                                self.events.append({
                                    "type": "rel", "dx": rel_dx,
                                    "dy": rel_dy, "time": now
                                })

                # --- Key / button events ---
                if event.type == e.EV_KEY:
                    if self.is_mapping and event.value == 1:
                        if event.code == e.KEY_ESC:
                            logger.info("Hotkey mapping cancelled.")
                        else:
                            if self.is_mapping == "rec":
                                self.hotkey_rec = event.code
                            else:
                                self.hotkey_play = event.code
                            logger.info("Hotkey remapped to %s", self.get_key_name(event.code))

                        try:
                            if self.is_mapping == "rec":
                                self.lbl_rec.configure(text=f"Record: {self.get_key_name(self.hotkey_rec)}", fg_color=['#3B8ED0', '#1F6AA5'])
                            else:
                                self.lbl_play.configure(text=f"Play/Stop: {self.get_key_name(self.hotkey_play)}", fg_color=['#3B8ED0', '#1F6AA5'])
                        except Exception as exc:
                            logger.debug("Failed to update button text during mapping: %s", exc)

                        self.is_mapping = None
                        continue

                    # Global hotkeys (on key press only).
                    # NOTE: never touch tkinter here — this runs on an evdev
                    # listener thread. Push to the queue; UI polls it.
                    if event.value == 1:
                        if event.code == self.hotkey_rec:
                            self._ui_queue.put("rec")
                        elif event.code == self.hotkey_play:
                            self._ui_queue.put("play")

                    with self.events_lock:
                        if self.recording:
                            if event.code not in [
                                self.hotkey_rec, self.hotkey_play
                            ]:
                                self.events.append({
                                    "type": "key", "code": event.code,
                                    "val": event.value,
                                    "time": time.monotonic() - self.start_time
                                })

        except OSError as exc:
            # This listener thread ends here, but the other devices keep
            # capturing — the recording itself must not crash. The loss is
            # recorded for the capture summary (once per device).
            logger.warning(
                "Device '%s' disconnected or unavailable: %s",
                self._safe_device_name(dev), exc
            )
            self._note_device_failure(dev, str(exc))
        except Exception as exc:
            logger.error(
                "Unexpected error on device '%s': %s",
                self._safe_device_name(dev), exc
            )
            self._note_device_failure(dev, str(exc))
