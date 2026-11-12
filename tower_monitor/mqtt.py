"""MQTT connection plus Home Assistant discovery.

* Availability: a last-will message sets <prefix>/status to "offline" if the
  Pi drops off, and "online" is published on every (re)connect.
* Discovery configs are retained on the broker, so they are only re-sent on
  the first connect and whenever HA announces it restarted
  (<discovery_prefix>/status = "online"). That keeps reconnects over
  cellular cheap.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
from typing import Any, Callable, Iterable

import paho.mqtt.client as mqtt

from .sensors.base import Entity

log = logging.getLogger(__name__)


def discovery_messages(
    entities: Iterable[Entity],
    *,
    group: str,
    expire_after: int | None,
    device_cfg: dict[str, Any],
    discovery_prefix: str,
    status_title: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Build (topic, payload) pairs for one sensor group's HA discovery."""
    dev_id = device_cfg["id"]
    prefix = device_cfg["topic_prefix"]
    device = {
        "identifiers": [dev_id],
        "name": device_cfg["name"],
        "manufacturer": "W4TRC",
        "model": "Raspberry Pi 4 + Sixfab 4G",
    }
    out = []
    for e in entities:
        uid = f"{dev_id}_{group}_{e.key}"
        payload: dict[str, Any] = {
            "name": e.name,
            "unique_id": uid,
            "device": device,
            "availability_topic": f"{prefix}/status",
        }
        if e.component in ("sensor", "binary_sensor"):
            payload["state_topic"] = f"{prefix}/{group}/state"
            payload["value_template"] = "{{ value_json.%s }}" % e.key
            if expire_after:
                payload["expire_after"] = expire_after
        if e.component == "binary_sensor":
            payload["payload_on"] = e.payload_on
            payload["payload_off"] = e.payload_off
        for attr, key in (
            (e.unit, "unit_of_measurement"),
            (e.device_class, "device_class"),
            (e.state_class, "state_class"),
            (e.icon, "icon"),
            (e.precision, "suggested_display_precision"),
        ):
            if attr is not None:
                payload[key] = attr
        if e.diagnostic:
            payload["entity_category"] = "diagnostic"
        payload.update(e.extra)
        topic = f"{discovery_prefix}/{e.component}/{dev_id}/{group}_{e.key}/config"
        out.append((topic, payload))

    if status_title:
        # Plain-text "ok" / error message for the whole group. No
        # expire_after: it should keep showing the last error.
        uid = f"{dev_id}_{group}_status"
        out.append((
            f"{discovery_prefix}/sensor/{dev_id}/{group}_status/config",
            {
                "name": f"{status_title} status",
                "unique_id": uid,
                "device": device,
                "availability_topic": f"{prefix}/status",
                "state_topic": f"{prefix}/{group}/status",
                "icon": "mdi:list-status",
                "entity_category": "diagnostic",
            },
        ))
    return out


class Publisher:
    def __init__(self, mqtt_cfg: dict[str, Any], device_cfg: dict[str, Any]):
        self.cfg = mqtt_cfg
        self.device_cfg = device_cfg
        self.prefix = device_cfg["topic_prefix"]
        self.availability_topic = f"{self.prefix}/status"
        self.connected = threading.Event()
        self._discovery: list[tuple[str, dict[str, Any]]] = []
        self._subscriptions: dict[str, Callable[[bytes], None]] = {}
        self._discovery_sent = False

        c = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=mqtt_cfg["client_id"],
        )
        if mqtt_cfg.get("username"):
            c.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password") or None)
        if mqtt_cfg.get("tls"):
            c.tls_set(
                ca_certs=mqtt_cfg.get("ca_certs") or None,
                cert_reqs=ssl.CERT_REQUIRED,
            )
        c.will_set(self.availability_topic, "offline", qos=1, retain=True)
        c.reconnect_delay_set(min_delay=5, max_delay=300)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c

    # ── lifecycle ─────────────────────────────────────────────
    def start(self) -> None:
        # connect_async + loop_start retries forever in the background, which
        # covers the modem/DNS not being ready yet at boot.
        self.client.connect_async(
            self.cfg["host"], int(self.cfg["port"]), keepalive=int(self.cfg["keepalive"])
        )
        self.client.loop_start()

    def stop(self) -> None:
        if self.connected.is_set():
            info = self.client.publish(self.availability_topic, "offline", qos=1, retain=True)
            info.wait_for_publish(timeout=5)
        self.client.disconnect()
        self.client.loop_stop()

    # ── registration ──────────────────────────────────────────
    def add_discovery(self, messages: list[tuple[str, dict[str, Any]]]) -> None:
        self._discovery.extend(messages)

    def subscribe(self, topic: str, handler: Callable[[bytes], None]) -> None:
        self._subscriptions[topic] = handler
        if self.connected.is_set():
            self.client.subscribe(topic, qos=1)

    # ── publishing ────────────────────────────────────────────
    def publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = True, qos: int = 0) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        self.client.publish(topic, msg, qos=qos, retain=retain)
        log.debug("→ %s %s", topic, msg)

    def publish_raw(self, topic: str, payload: bytes | str, *, retain: bool = True, qos: int = 1) -> None:
        self.client.publish(topic, payload, qos=qos, retain=retain)
        log.debug("→ %s (%d bytes)", topic, len(payload))

    def send_discovery(self) -> None:
        for topic, payload in self._discovery:
            self.publish_json(topic, payload, retain=True, qos=1)
        self._discovery_sent = True
        log.info("Published %d discovery configs", len(self._discovery))

    # ── callbacks ─────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("Connected to MQTT %s:%s", self.cfg["host"], self.cfg["port"])
        client.publish(self.availability_topic, "online", qos=1, retain=True)
        ha_status = f"{self.cfg['discovery_prefix']}/status"
        client.subscribe(ha_status, qos=1)
        for topic in self._subscriptions:
            client.subscribe(topic, qos=1)
        if not self._discovery_sent:
            self.send_discovery()
        self.connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        log.warning("MQTT disconnected: %s (will retry)", reason_code)

    def _on_message(self, client, userdata, msg):
        if msg.topic == f"{self.cfg['discovery_prefix']}/status":
            if msg.payload == b"online":
                log.info("Home Assistant restarted; re-sending discovery")
                self.send_discovery()
            return
        handler = self._subscriptions.get(msg.topic)
        if handler:
            try:
                handler(msg.payload)
            except Exception:
                log.exception("Handler for %s failed", msg.topic)
