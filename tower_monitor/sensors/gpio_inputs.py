"""GPIO inputs: mains present/absent and the shack door.

Wiring (both use the Pi's internal pull-up, so no resistors needed):
  Door:  GPIO -> reed switch -> GND. Magnet next to switch (door closed)
         closes the contact and pulls the pin LOW.
  Mains: 5V USB charger on mains -> optocoupler module input. Opto output
         between GPIO and GND; charger powered = opto on = pin LOW.

A change publishes immediately; the state is also re-sent every
`interval` so HA's expire_after can tell a dead Pi from a quiet door.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .base import Entity, Sensor

log = logging.getLogger(__name__)


class _GpioInput(Sensor):
    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        self.pin = int(cfg["gpio"])
        self.debounce = float(cfg.get("debounce", 0.1))
        self._dev = None

    def setup(self):
        if self.simulate:
            return
        from gpiozero import DigitalInputDevice
        # pull_up=True: is_active is True when the pin is pulled LOW.
        self._dev = DigitalInputDevice(self.pin, pull_up=True, bounce_time=self.debounce)
        self._dev.when_activated = self._changed
        self._dev.when_deactivated = self._changed

    def _pin_low(self) -> bool:
        return bool(self._dev.is_active)

    def _changed(self, *_):
        self.trigger()

    def close(self):
        if self._dev is not None:
            self._dev.close()
            self._dev = None


class MainsSensor(_GpioInput):
    group = "mains"
    title = "Mains"

    def entities(self):
        return [Entity("mains", "Mains power", component="binary_sensor", device_class="power")]

    def _changed(self, *_):
        log.warning("Mains power %s", "restored" if self._present() else "LOST")
        super()._changed()

    def _present(self) -> bool:
        low = self._pin_low()
        return low if self.cfg.get("present_when_low", True) else not low

    def read(self) -> dict[str, Any]:
        if self.simulate:
            return {"mains": "ON"}
        return {"mains": "ON" if self._present() else "OFF"}


class DoorSensor(_GpioInput):
    group = "door"
    title = "Door"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        self.store = None                         # StateStore, set by build()
        self.on_open: Callable[[], None] = lambda: None
        self._was_open: bool | None = None

    def entities(self):
        return [
            Entity("door", "Shack door", component="binary_sensor", device_class="door"),
            Entity("last_opened", "Shack door last opened", device_class="timestamp",
                   icon="mdi:door-open"),
        ]

    def _is_open(self) -> bool:
        low = self._pin_low()
        closed = low if self.cfg.get("closed_when_low", True) else not low
        return not closed

    def _changed(self, *_):
        is_open = self._is_open()
        if is_open and self._was_open is not True:
            log.info("Door opened")
            if self.store is not None:
                self.store.set("door_last_opened", datetime.now(timezone.utc).isoformat())
            self.on_open()
        elif not is_open and self._was_open:
            log.info("Door closed")
        self._was_open = is_open
        super()._changed()

    def setup(self):
        super().setup()
        if not self.simulate:
            self._was_open = self._is_open()

    def read(self) -> dict[str, Any]:
        is_open = False if self.simulate else self._is_open()
        last = self.store.get("door_last_opened") if self.store else None
        return {"door": "ON" if is_open else "OFF", "last_opened": last}
