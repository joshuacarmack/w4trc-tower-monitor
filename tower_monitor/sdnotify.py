"""Minimal systemd sd_notify (READY / WATCHDOG) with no dependencies.

Does nothing when not running under systemd.
"""

from __future__ import annotations

import os
import socket


def notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(state.encode())
    except OSError:
        pass


def ready() -> None:
    notify("READY=1")


def watchdog() -> None:
    notify("WATCHDOG=1")
