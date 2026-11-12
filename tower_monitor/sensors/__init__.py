"""Sensor registry. Modules are imported lazily so a missing hardware
library only affects the sensor that needs it."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from ..state import StateStore
from .base import Sensor

log = logging.getLogger(__name__)

# config section -> (module, class)
REGISTRY: dict[str, tuple[str, str]] = {
    "environment": ("environment", "EnvironmentSensor"),
    "system": ("system", "SystemSensor"),
    "cellular": ("cellular", "CellularSensor"),
    "data_usage": ("data_usage", "DataUsageSensor"),
    "mains": ("gpio_inputs", "MainsSensor"),
    "door": ("gpio_inputs", "DoorSensor"),
    "renogy": ("renogy", "RenogySensor"),
    "camera": ("camera", "Camera"),
}


def build(cfg: dict[str, Any], simulate: bool = False) -> list[Sensor]:
    store = StateStore(Path(cfg["device"]["state_dir"]) / "state.json")
    sensors: dict[str, Sensor] = {}
    for section, (module, cls_name) in REGISTRY.items():
        sec = cfg.get(section, {})
        if not sec.get("enabled", False):
            continue
        mod = importlib.import_module(f".{module}", __name__)
        cls = getattr(mod, cls_name)
        sensor = cls(sec, simulate=simulate, full_cfg=cfg)
        if hasattr(sensor, "store"):
            sensor.store = store
        sensors[section] = sensor

    door, camera = sensors.get("door"), sensors.get("camera")
    if door and camera:
        door.on_open = camera.request_capture
    return list(sensors.values())
