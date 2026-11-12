"""Sensor framework.

Each sensor group is a small class that:
  * declares the Home Assistant entities it provides (entities()),
  * returns a flat dict of readings (read()), raising on failure.

The runner publishes read() results as JSON to <prefix>/<group>/state and
publishes "ok" or the error text to <prefix>/<group>/status, so a broken
sensor shows up in HA as an error instead of just going quiet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Entity:
    key: str                          # key in the state JSON
    name: str                         # friendly name in HA
    component: str = "sensor"         # "sensor" or "binary_sensor"
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    diagnostic: bool = False
    precision: int | None = None      # suggested_display_precision
    payload_on: str = "ON"            # binary_sensor only
    payload_off: str = "OFF"
    extra: dict[str, Any] = field(default_factory=dict)


class Sensor:
    group: str = "base"               # topic segment and unique_id segment
    title: str = "Base"               # used for the "<title> status" entity

    def __init__(self, cfg: dict[str, Any], simulate: bool = False,
                 full_cfg: dict[str, Any] | None = None):
        self.cfg = cfg
        self.full_cfg = full_cfg or {}
        self.simulate = simulate
        self.interval: float = float(cfg.get("interval", 600))
        # Set by the runner; call it to request an immediate publish.
        self.trigger: Callable[[], None] = lambda: None

    def setup(self) -> None:
        """Open hardware. May raise; the runner retries on the next cycle."""

    def entities(self) -> list[Entity]:
        raise NotImplementedError

    def read(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass
