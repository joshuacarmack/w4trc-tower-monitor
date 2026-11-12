"""Main loop: schedule each sensor group, publish results, report errors."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import sdnotify
from .mqtt import Publisher, discovery_messages
from .sensors.base import Sensor

log = logging.getLogger(__name__)

MAX_STATUS_LEN = 200
SETUP_RETRY = 60.0  # seconds between hardware setup attempts


class Runner:
    def __init__(self, cfg: dict[str, Any], sensors: list[Sensor], publisher: Publisher):
        self.cfg = cfg
        self.sensors = sensors
        self.pub = publisher
        self.prefix = cfg["device"]["topic_prefix"]
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._next_due: dict[str, float] = {s.group: 0.0 for s in sensors}
        self._is_setup: dict[str, bool] = {s.group: False for s in sensors}
        self._setup_retry: dict[str, float] = {s.group: 0.0 for s in sensors}
        self._last_status: dict[str, str] = {}

        for s in sensors:
            s.trigger = (lambda g=s.group: self.request(g))
            self.pub.add_discovery(discovery_messages(
                s.entities(),
                group=s.group,
                expire_after=int(s.interval * 3 + 60),
                device_cfg=cfg["device"],
                discovery_prefix=cfg["mqtt"]["discovery_prefix"],
                status_title=s.title,
            ))
            attach = getattr(s, "attach", None)
            if attach:
                attach(self.pub)

    # Called from other threads (GPIO callbacks, MQTT handlers).
    def request(self, group: str) -> None:
        with self._lock:
            self._pending.add(group)
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        self.pub.start()
        sdnotify.ready()
        was_connected = False
        try:
            while not self._stop.is_set():
                sdnotify.watchdog()
                connected = self.pub.connected.is_set()
                if connected and not was_connected:
                    # Fresh connection: send everything now rather than
                    # waiting out each interval.
                    for g in self._next_due:
                        self._next_due[g] = 0.0
                    self._last_status.clear()
                was_connected = connected

                # Set up hardware even while offline so GPIO edge callbacks
                # (door -> photo) work and get queued.
                now = time.monotonic()
                for s in self.sensors:
                    if not self._is_setup[s.group] and now >= self._setup_retry[s.group]:
                        self._setup_retry[s.group] = now + SETUP_RETRY
                        self._try_setup(s)

                if connected:
                    now = time.monotonic()
                    with self._lock:
                        pending, self._pending = self._pending, set()
                    for s in self.sensors:
                        if s.group in pending or now >= self._next_due[s.group]:
                            self._run_one(s)
                            self._next_due[s.group] = time.monotonic() + s.interval

                timeout = 10.0
                if connected:
                    soonest = min(self._next_due.values(), default=time.monotonic() + 10)
                    timeout = max(0.5, min(10.0, soonest - time.monotonic()))
                self._wake.wait(timeout)
                self._wake.clear()
        finally:
            for s in self.sensors:
                try:
                    s.close()
                except Exception:
                    log.exception("Error closing %s", s.group)
            self.pub.stop()

    # ── internals ─────────────────────────────────────────────
    def _try_setup(self, s: Sensor) -> bool:
        try:
            s.setup()
            self._is_setup[s.group] = True
            log.info("%s: ready", s.group)
            return True
        except Exception as e:
            self._set_status(s, f"setup failed: {e}")
            return False

    def _run_one(self, s: Sensor) -> None:
        if not self._is_setup[s.group] and not self._try_setup(s):
            return
        try:
            data = s.read()
        except Exception as e:
            log.warning("%s: read failed: %s", s.group, e)
            self._set_status(s, f"error: {e}")
            # Re-open hardware next time; fixes many I2C/serial hiccups.
            try:
                s.close()
            except Exception:
                pass
            self._is_setup[s.group] = False
            return
        if data:
            self.pub.publish_json(f"{self.prefix}/{s.group}/state", data)
        self._set_status(s, "ok")

    def _set_status(self, s: Sensor, status: str) -> None:
        status = status[:MAX_STATUS_LEN]
        if self._last_status.get(s.group) == status:
            return
        if status != "ok":
            log.warning("%s: %s", s.group, status)
        self._last_status[s.group] = status
        # If offline this is dropped; _last_status is cleared on reconnect so
        # the next read re-sends it.
        if self.pub.connected.is_set():
            self.pub.publish_raw(f"{self.prefix}/{s.group}/status", status, qos=1)
