"""On-demand Tailscale: HA buttons to turn it on/off, plus its current state.

Tailscale's relay keepalives cost ~9 MiB/day over cellular, so in on-demand
mode tailscaled only runs for a set time (see scripts/tower-tailscale). This
service runs unprivileged (NoNewPrivileges), so it never starts or stops
tailscaled itself: it writes the request to a file that a root systemd
path unit (tower-tailscale-request.path) acts on.

A watcher thread checks `systemctl is-active tailscaled` locally every
WATCH_SECONDS and only publishes when the state changes, so the state stays
current without spending data.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Entity, Sensor

log = logging.getLogger(__name__)

WATCH_SECONDS = 20
MAX_MINUTES = 480


def parse_command(payload: bytes) -> str | None:
    """Turn an MQTT payload into a request-file line, or None if invalid."""
    text = payload.decode("ascii", "ignore").strip().lower()
    if text in ("on", "off"):
        return text
    if text.isdigit() and 1 <= int(text) <= MAX_MINUTES:
        return str(int(text))
    return None


def read_off_at(path: Path) -> str | None:
    """ISO timestamp from the helper's epoch file, or None."""
    try:
        epoch = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class TailscaleSensor(Sensor):
    group = "tailscale"
    title = "Tailscale"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        prefix = self.full_cfg.get("device", {}).get("topic_prefix", "tower")
        state_dir = self.full_cfg.get("device", {}).get("state_dir", "/var/lib/tower-monitor")
        self.command_topic = f"{prefix}/tailscale/cmd"
        self.request_file = Path(cfg.get("request_file") or Path(state_dir) / "tailscale-request")
        self.off_at_file = Path(cfg.get("off_at_file", "/run/tower-tailscale-off-at"))
        self._sim_running = False
        self._last: tuple[bool, str | None] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── wiring ────────────────────────────────────────────────
    def attach(self, pub) -> None:
        pub.subscribe(self.command_topic, self.handle_command)

    def entities(self):
        return [
            Entity("running", "Tailscale", component="binary_sensor",
                   device_class="running", icon="mdi:vpn"),
            Entity("off_at", "Tailscale turns off at", device_class="timestamp",
                   icon="mdi:timer-off-outline"),
            Entity("on", "Tailscale on (1 h)", component="button", icon="mdi:vpn",
                   extra={"command_topic": self.command_topic, "payload_press": "on"}),
            Entity("off", "Tailscale off", component="button", icon="mdi:lan-disconnect",
                   extra={"command_topic": self.command_topic, "payload_press": "off"}),
        ]

    def setup(self):
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._watch, name="tailscale-watch", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        self._thread = None

    # ── commands (MQTT thread) ────────────────────────────────
    def handle_command(self, payload: bytes) -> None:
        req = parse_command(payload)
        if req is None:
            log.warning("Ignoring Tailscale command %r", payload[:32])
            return
        log.info("Tailscale request: %s", req)
        if self.simulate:
            self._sim_running = req != "off"
        else:
            # Plain write (no rename): the path unit fires on close-after-write.
            self.request_file.write_text(req + "\n")
        self.trigger()

    # ── state ─────────────────────────────────────────────────
    def _state(self) -> tuple[bool, str | None]:
        if self.simulate:
            return self._sim_running, None
        r = subprocess.run(["systemctl", "is-active", "--quiet", "tailscaled"], timeout=10)
        running = r.returncode == 0
        return running, read_off_at(self.off_at_file) if running else None

    def _watch(self) -> None:
        while not self._stop.wait(WATCH_SECONDS):
            try:
                state = self._state()
            except (OSError, subprocess.SubprocessError):
                continue
            if self._last is not None and state != self._last:
                self.trigger()

    def read(self) -> dict[str, Any]:
        running, off_at = self._state()
        self._last = (running, off_at)
        return {"running": "ON" if running else "OFF", "off_at": off_at}
