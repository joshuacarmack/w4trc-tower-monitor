from tower_monitor import config
from tower_monitor.mqtt import discovery_messages
from tower_monitor.sensors.base import Entity

DEVICE = {"id": "tower_monitor", "name": "Tower Monitor", "topic_prefix": "tower"}


def test_config_merge_keeps_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[mqtt]\nhost = "broker"\n[camera]\nenabled = false\n')
    cfg = config.load(p)
    assert cfg["mqtt"]["host"] == "broker"
    assert cfg["mqtt"]["port"] == 1883          # default kept
    assert cfg["camera"]["enabled"] is False
    assert cfg["camera"]["max_sent_per_day"] == 3


def test_sensor_discovery_payload():
    msgs = discovery_messages(
        [Entity("temperature", "Shack temperature", unit="°C",
                device_class="temperature", state_class="measurement")],
        group="environment", expire_after=1860, device_cfg=DEVICE,
        discovery_prefix="homeassistant", status_title="Environment",
    )
    topic, payload = msgs[0]
    assert topic == "homeassistant/sensor/tower_monitor/environment_temperature/config"
    assert payload["state_topic"] == "tower/environment/state"
    assert payload["value_template"] == "{{ value_json.temperature }}"
    assert payload["availability_topic"] == "tower/status"
    assert payload["expire_after"] == 1860
    assert payload["unique_id"] == "tower_monitor_environment_temperature"

    status_topic, status = msgs[1]
    assert status_topic.endswith("/environment_status/config")
    assert status["state_topic"] == "tower/environment/status"
    assert "expire_after" not in status
    assert status["entity_category"] == "diagnostic"


def test_binary_and_image_discovery():
    msgs = discovery_messages(
        [
            Entity("door", "Door", component="binary_sensor", device_class="door"),
            Entity("photo", "Door photo", component="image",
                   extra={"image_topic": "tower/camera/image", "content_type": "image/jpeg"}),
        ],
        group="door", expire_after=100, device_cfg=DEVICE,
        discovery_prefix="homeassistant",
    )
    (_, door), (img_topic, img) = msgs
    assert door["payload_on"] == "ON"
    assert img_topic.startswith("homeassistant/image/")
    assert "state_topic" not in img and "expire_after" not in img
    assert img["image_topic"] == "tower/camera/image"
