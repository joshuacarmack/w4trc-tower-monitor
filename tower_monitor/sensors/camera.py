"""Door-triggered photos from a USB webcam (IR model for the dark shack).

Flow on a door-open (or the HA "Take photo" button):
  1. wait `delay` seconds so the person is in frame,
  2. capture `burst` full-size JPEGs to storage_dir with fswebcam,
  3. mark the middle one as pending,
  4. on the next runner cycle while MQTT is connected, resize it and
     publish it (retained) to <prefix>/camera/image for an HA image entity.

At most `max_sent_per_day` photos go over cellular per day (persisted, so
a reboot doesn't reset it). Full-size photos stay on the Pi for keep_days;
copy them off over Tailscale with scp/rsync.
"""

from __future__ import annotations

import io
import logging
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import Entity, Sensor

log = logging.getLogger(__name__)


class DailyCounter:
    """Persisted 'photos sent today' count."""

    def __init__(self, store, key: str = "photos_sent"):
        self.store, self.key = store, key

    def count(self, today: date | None = None) -> int:
        today = today or date.today()
        rec = self.store.get(self.key) or {}
        return rec.get("count", 0) if rec.get("date") == today.isoformat() else 0

    def increment(self, today: date | None = None) -> int:
        today = today or date.today()
        n = self.count(today) + 1
        self.store.set(self.key, {"date": today.isoformat(), "count": n})
        return n


def shrink_jpeg(data: bytes, width: int, quality: int) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


class Camera(Sensor):
    group = "camera"
    title = "Camera"

    def __init__(self, cfg, simulate=False, full_cfg=None):
        super().__init__(cfg, simulate, full_cfg)
        prefix = self.full_cfg.get("device", {}).get("topic_prefix", "tower")
        self.image_topic = f"{prefix}/camera/image"
        self.command_topic = f"{prefix}/camera/capture"
        self.dir = Path(cfg.get("storage_dir", "/var/lib/tower-monitor/photos"))
        self.store = None                 # StateStore, set by build()
        self.pub = None
        self._busy = threading.Lock()
        self._last_prune = 0.0

    # ── wiring ────────────────────────────────────────────────
    def attach(self, pub) -> None:
        self.pub = pub
        pub.subscribe(self.command_topic, lambda payload: self.request_capture())

    def entities(self):
        return [
            Entity("photo", "Door photo", component="image",
                   extra={"image_topic": self.image_topic, "content_type": "image/jpeg"}),
            Entity("capture", "Take photo", component="button", icon="mdi:camera",
                   extra={"command_topic": self.command_topic}),
            Entity("last_photo", "Last photo taken", device_class="timestamp",
                   icon="mdi:camera-timer"),
            Entity("sent_today", "Photos sent today", icon="mdi:image-multiple",
                   state_class="measurement", diagnostic=True),
            Entity("stored", "Photos stored on Pi", icon="mdi:folder-image",
                   state_class="measurement", diagnostic=True),
        ]

    def setup(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.simulate and not Path(self.cfg.get("device", "/dev/video0")).exists():
            raise RuntimeError(f"camera {self.cfg.get('device')} not found (is it plugged in?)")

    # ── capture (runs in its own thread) ──────────────────────
    def request_capture(self) -> None:
        if not self._busy.acquire(blocking=False):
            log.info("Capture already in progress; ignoring trigger")
            return
        threading.Thread(target=self._capture_worker, name="camera", daemon=True).start()

    def _capture_worker(self) -> None:
        try:
            time.sleep(float(self.cfg.get("delay", 2.0)))
            burst = max(1, int(self.cfg.get("burst", 3)))
            stamp = base = datetime.now().strftime("%Y%m%d-%H%M%S")
            n = 1
            while any(self.dir.glob(f"{stamp}-*.jpg")):   # two events in one second
                n += 1
                stamp = f"{base}_{n}"
            files: list[Path] = []
            for i in range(burst):
                path = self.dir / f"{stamp}-{i + 1}.jpg"
                try:
                    self._capture(path)
                    files.append(path)
                except Exception as e:
                    log.error("Capture %d failed: %s", i + 1, e)
                if i < burst - 1:
                    time.sleep(float(self.cfg.get("burst_interval", 1.5)))
            if not files:
                return
            log.info("Saved %d photo(s) %s-*.jpg", len(files), stamp)
            if self.store is not None:
                self.store.set("last_photo", datetime.now(timezone.utc).isoformat())
                self.store.set("pending_photo", str(files[len(files) // 2]))
            self.trigger()  # runner calls read(), which sends it when online
        finally:
            self._busy.release()

    def _capture(self, path: Path) -> None:
        if self.simulate:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", tuple(int(x) for x in
                            self.cfg.get("capture_resolution", "1280x720").split("x")), "gray")
            ImageDraw.Draw(img).text((20, 20), f"SIMULATED {datetime.now():%c}", fill="white")
            img.save(path, "JPEG", quality=90)
            return
        cmd = [
            "fswebcam", "-q", "--no-banner",
            "-d", self.cfg.get("device", "/dev/video0"),
            "-r", self.cfg.get("capture_resolution", "1280x720"),
            "-S", str(int(self.cfg.get("skip_frames", 30))),
            "--jpeg", "90",
            str(path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"fswebcam failed: {(r.stderr or r.stdout).strip()[:150]}")

    # ── periodic / triggered publish (runner thread) ──────────
    def read(self) -> dict[str, Any]:
        sent_today = DailyCounter(self.store).count() if self.store else 0
        pending = self.store.get("pending_photo") if self.store else None
        if pending and self.pub is not None and self.pub.connected.is_set():
            sent_today = self._send(Path(pending), sent_today)
        if time.monotonic() - self._last_prune > 86400:
            self._prune()
            self._last_prune = time.monotonic()
        return {
            "last_photo": self.store.get("last_photo") if self.store else None,
            "sent_today": sent_today,
            "stored": sum(1 for _ in self.dir.glob("*.jpg")) if self.dir.exists() else 0,
        }

    def _send(self, path: Path, sent_today: int) -> int:
        limit = int(self.cfg.get("max_sent_per_day", 3))
        self.store.set("pending_photo", None)
        if sent_today >= limit:
            log.warning("Daily photo limit (%d) reached; %s kept on Pi only", limit, path.name)
            return sent_today
        try:
            data = shrink_jpeg(path.read_bytes(), int(self.cfg.get("send_width", 640)),
                               int(self.cfg.get("send_quality", 60)))
        except Exception as e:
            log.error("Could not prepare %s: %s", path, e)
            return sent_today
        self.pub.publish_raw(self.image_topic, data, qos=1, retain=True)
        log.info("Sent %s (%d KB)", path.name, len(data) // 1024)
        return DailyCounter(self.store).increment()

    def _prune(self) -> None:
        keep = timedelta(days=int(self.cfg.get("keep_days", 120)))
        cutoff = time.time() - keep.total_seconds()
        for p in self.dir.glob("*.jpg"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
