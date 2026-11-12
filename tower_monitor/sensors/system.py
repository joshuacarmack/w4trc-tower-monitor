"""Pi health: CPU temp, uptime, disk, memory, and undervoltage flags.

Undervoltage matters here: the Pi runs from the battery through a 12V->5V
converter, and a sagging converter shows up as the Pi's undervoltage bit
long before it causes crashes.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from typing import Any

from .base import Entity, Sensor

# vcgencmd get_throttled bits
UNDERVOLT_NOW = 1 << 0
THROTTLED_NOW = 1 << 2
UNDERVOLT_SINCE_BOOT = 1 << 16
THROTTLED_SINCE_BOOT = 1 << 18


def parse_throttled(text: str) -> int:
    # "throttled=0x50005"
    return int(text.strip().split("=", 1)[1], 16)


def _on(flag: bool) -> str:
    return "ON" if flag else "OFF"


class SystemSensor(Sensor):
    group = "system"
    title = "System"

    def entities(self):
        return [
            Entity("cpu_temp", "CPU temperature", unit="°C", device_class="temperature",
                   state_class="measurement", precision=0, diagnostic=True),
            Entity("uptime_hours", "Uptime", unit="h", device_class="duration",
                   state_class="measurement", precision=1, diagnostic=True),
            Entity("disk_used_pct", "Disk used", unit="%", state_class="measurement",
                   icon="mdi:harddisk", precision=0, diagnostic=True),
            Entity("mem_used_pct", "Memory used", unit="%", state_class="measurement",
                   icon="mdi:memory", precision=0, diagnostic=True),
            Entity("undervoltage", "Pi undervoltage now", component="binary_sensor",
                   device_class="problem", diagnostic=True),
            Entity("undervoltage_since_boot", "Pi undervoltage since boot",
                   component="binary_sensor", device_class="problem", diagnostic=True),
            Entity("throttled_since_boot", "Pi throttled since boot",
                   component="binary_sensor", device_class="problem", diagnostic=True),
        ]

    def read(self) -> dict[str, Any]:
        if self.simulate:
            return {
                "cpu_temp": round(45 + random.uniform(-3, 3), 1),
                "uptime_hours": 12.5, "disk_used_pct": 31.0, "mem_used_pct": 22.0,
                "undervoltage": "OFF", "undervoltage_since_boot": "OFF",
                "throttled_since_boot": "OFF",
            }
        out: dict[str, Any] = {}
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            out["cpu_temp"] = round(int(f.read().strip()) / 1000, 1)
        with open("/proc/uptime") as f:
            out["uptime_hours"] = round(float(f.read().split()[0]) / 3600, 2)
        du = shutil.disk_usage("/")
        out["disk_used_pct"] = round(100 * du.used / du.total, 1)
        out["mem_used_pct"] = _mem_used_pct()

        try:
            r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                               text=True, timeout=5, check=True)
            flags = parse_throttled(r.stdout)
            out["undervoltage"] = _on(bool(flags & UNDERVOLT_NOW))
            out["undervoltage_since_boot"] = _on(bool(flags & UNDERVOLT_SINCE_BOOT))
            out["throttled_since_boot"] = _on(bool(flags & THROTTLED_SINCE_BOOT))
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            # vcgencmd missing or user not in the 'video' group; the rest
            # of the readings are still useful.
            pass
        return out


def _mem_used_pct() -> float:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
    return round(100 * (total - avail) / total, 1)
