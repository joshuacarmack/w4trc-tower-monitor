"""Cellular data used, from vnstat (persists across reboots).

vnstat counts IP traffic on the interface; the carrier may bill a little
more (or round up per session), so set the HA alert with some margin.
If the plan's billing cycle doesn't start on the 1st, set MonthRotate in
/etc/vnstat.conf to the billing day.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from typing import Any

from .base import Entity, Sensor

MB = 1_000_000


def parse_vnstat(text: str, interface: str, today: date) -> dict[str, Any]:
    data = json.loads(text)
    ifaces = data.get("interfaces", [])
    iface = next((i for i in ifaces if i.get("name") == interface), None)
    if iface is None:
        names = ", ".join(i.get("name", "?") for i in ifaces) or "none"
        raise RuntimeError(f"vnstat has no data for {interface} (has: {names})")
    traffic = iface.get("traffic", {})

    def total(entries, match) -> float:
        for e in entries or []:
            if match(e.get("date", {})):
                return (e.get("rx", 0) + e.get("tx", 0)) / MB
        return 0.0

    month = total(traffic.get("month"),
                  lambda d: d.get("year") == today.year and d.get("month") == today.month)
    day = total(traffic.get("day"),
                lambda d: (d.get("year"), d.get("month"), d.get("day"))
                == (today.year, today.month, today.day))
    return {"month_mb": round(month, 2), "today_mb": round(day, 2)}


class DataUsageSensor(Sensor):
    group = "data_usage"
    title = "Data usage"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        self.interface = cfg.get("interface", "wwan0")

    def entities(self):
        return [
            Entity("month_mb", "Cellular data this month", unit="MB", device_class="data_size",
                   state_class="total_increasing", icon="mdi:counter", precision=1),
            Entity("today_mb", "Cellular data today", unit="MB", device_class="data_size",
                   state_class="total_increasing", icon="mdi:counter", precision=2,
                   diagnostic=True),
        ]

    def read(self) -> dict[str, Any]:
        if self.simulate:
            return {"month_mb": 12.34, "today_mb": 0.41}
        r = subprocess.run(["vnstat", "--json", "-i", self.interface],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"vnstat failed: {(r.stderr or r.stdout).strip()}")
        return parse_vnstat(r.stdout, self.interface, date.today())
