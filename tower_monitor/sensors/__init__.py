"""Sensor registry. Modules are imported lazily so a missing hardware
library only affects the sensor that needs it."""

from __future__ import annotations

import importlib
import logging
from typing import Any

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
    sensors: list[Sensor] = []
    for section, (module, cls_name) in REGISTRY.items():
        sec = cfg.get(section, {})
        if not sec.get("enabled", False):
            continue
        mod = importlib.import_module(f".{module}", __name__)
        cls = getattr(mod, cls_name)
        sensors.append(cls(sec, simulate=simulate, full_cfg=cfg))
    return sensors
