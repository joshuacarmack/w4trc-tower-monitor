# W4TRC Tower Monitor

Raspberry Pi 4 + Sixfab 4G HAT at the tower shack. Publishes to MQTT with
Home Assistant discovery; HA does dashboards and notifications.

| Group | What | Source |
|---|---|---|
| Environment | Shack temperature, humidity, dew point (+ pressure on BME280) | SHT41 or BME280 on I2C |
| Mains | Mains power present / lost | 5 V USB charger → optocoupler → GPIO |
| Door | Open/closed, last-opened time | Reed switch → GPIO |
| Camera | Door photo, "Take photo" button, photos sent today | IR USB webcam + fswebcam |
| Solar controller | Battery V / SOC, panel V/A/W, load, daily Ah & Wh, charge state, faults | Renogy Rover 20A, RS232 Modbus |
| System | CPU temp, uptime, disk, memory, **Pi undervoltage** | Pi |
| Cellular | Signal, technology, operator, registration | ModemManager (`mmcli`) |
| Data usage | MB this month / today | vnstat |

Each group also has a diagnostic **"… status"** entity in HA that reads `ok`
or the error (e.g. `error: no response from SHT4x at 0x44 (check wiring…)`).
A dead sensor shows up as an error instead of going quiet.

**Availability:** the Pi sets an MQTT last will, so `tower/status` flips to
`offline` within ~1.5× keepalive (≈7.5 min) of the Pi dropping off, and every
entity goes *unavailable*. Each reading also has `expire_after`, so stale
values never look current.

## Data budget (100 MB/month)

Rough expectation: sensor traffic + MQTT keepalives ≈ 10–15 MB (≈ 20k small
messages; TCP/WireGuard overhead is most of it), Tailscale background
≈ 5–10 MB, photos ≈ 40 KB each with a hard cap of `max_sent_per_day`
(default 3). Measure it during burn-in with the
*Cellular data this month* entity and tune `interval`s if needed.

Run `sudo ./scripts/cellular-data-saver.sh` on the Pi to stop apt and other
timers from downloading over cellular.

## Wiring

Pi header pins (physical numbering). Check the Sixfab HAT's docs to make
sure it doesn't use GPIO17/27 on your HAT revision; change `gpio =` in the
config if it does.

| Signal | Pi pin | Notes |
|---|---|---|
| I2C 3.3 V | 1 | SHT41 / BME280 VIN |
| I2C SDA (GPIO2) | 3 | |
| I2C SCL (GPIO3) | 5 | |
| GND | 6, 9, 14 | |
| Door (GPIO17) | 11 | Reed switch between pin 11 and GND. Closed door = LOW |
| Mains (GPIO27) | 13 | Opto output collector → pin 13, emitter → GND |

* **Temp sensor:** keep the I2C cable short (< 1 m) and mount the sensor
  away from the Pi and the converter, which run warm.
* **Mains detect:** a cheap 5 V USB wall charger plugged into a mains outlet
  feeds the optocoupler module's *input*. Mains never touches the Pi. The
  Pi itself must run from the battery (it does, via the 12 V → 5 V converter),
  otherwise it can't report a mains failure.
* **Renogy Rover:** USB-RS232 adapter (genuine FTDI) → DB9 → RJ12 into the
  Rover's RS232 port. Some RJ12 pins on the Rover carry supply voltage:
  **meter the port and confirm TX/RX/GND against Renogy's pinout before
  connecting.** Use the `/dev/serial/by-id/...` path in the config so the
  port name never moves.
* **Camera:** any UVC USB webcam with IR LEDs and auto IR-cut. Night shots
  come out black and white.

## Bench bring-up

1. Flash Raspberry Pi OS Lite (64-bit). Re-install Sixfab and Tailscale.
2. Clone this repo onto the Pi and run `sudo ./scripts/install.sh`.
3. Edit `/etc/tower-monitor/config.toml` (MQTT host, user, password). Set
   `enabled = false` for anything not wired yet.
4. Check hardware one piece at a time:
   * `i2cdetect -y 1` → `44` (SHT4x) or `76`/`77` (BME280)
   * `ls /dev/serial/by-id/` → the FTDI adapter
   * `fswebcam -d /dev/video0 -r 1280x720 -S 30 test.jpg`
   * `mmcli -L` and `ip -br link` (is the modem `wwan0` or `usb0`?)
   * `vnstat --iflist`
5. Try it in the foreground first:
   `sudo -u towermon /opt/tower-monitor/venv/bin/python -m tower_monitor -c /etc/tower-monitor/config.toml -v`
6. Then `sudo systemctl start tower-monitor` and `journalctl -u tower-monitor -f`.
7. In HA the device **Tower Monitor** appears under the MQTT integration.
8. Tests: unplug the charger (mains alert), open the door (photo), pull the
   network (offline).
9. Run `sudo ./scripts/cellular-data-saver.sh` and burn in on cellular only
   for a few days, watching data use.

**No hardware handy?** `python -m tower_monitor -c config.toml --simulate -v`
publishes fake readings for every sensor (works on any PC) so you can build
the HA dashboard and automations first.

## Home Assistant automations (examples)

Entity IDs depend on HA's naming; check them under the device page.

```yaml
- alias: Tower - mains lost
  triggers:
    - trigger: state
      entity_id: binary_sensor.tower_monitor_mains_power
      to: "off"
      for: "00:02:00"
  actions:
    - action: notify.notify
      data:
        message: "Tower mains power LOST. Battery {{ states('sensor.tower_monitor_battery_voltage') }} V"

- alias: Tower - door opened
  triggers:
    - trigger: state
      entity_id: binary_sensor.tower_monitor_shack_door
      to: "on"
  actions:
    - action: notify.notify
      data:
        message: "Tower shack door opened"

- alias: Tower - monitor offline
  triggers:
    - trigger: state
      entity_id: sensor.tower_monitor_shack_temperature
      to: unavailable
      for: "00:20:00"
  actions:
    - action: notify.notify
      data:
        message: "Tower monitor is offline"

- alias: Tower - battery low
  triggers:
    - trigger: numeric_state
      entity_id: sensor.tower_monitor_battery_voltage
      below: 12.0
      for: "00:10:00"
  actions:
    - action: notify.notify
      data:
        message: "Tower battery low: {{ trigger.to_state.state }} V"

- alias: Tower - data plan 75%
  triggers:
    - trigger: numeric_state
      entity_id: sensor.tower_monitor_cellular_data_this_month
      above: 75
  actions:
    - action: notify.notify
      data:
        message: "Tower has used {{ trigger.to_state.state }} MB of 100 MB this month"
```

## Getting the full-size photos

They're kept on the Pi for `keep_days`:

```sh
scp -r tower-pi:/var/lib/tower-monitor/photos ./tower-photos   # over Tailscale
```

## MQTT topics

| Topic | Payload |
|---|---|
| `tower/status` | `online` / `offline` (retained, last will) |
| `tower/<group>/state` | JSON readings (retained) |
| `tower/<group>/status` | `ok` or error text (retained) |
| `tower/camera/image` | JPEG bytes (retained) |
| `tower/camera/capture` | any payload → take a photo (HA button) |

## Development

```sh
python -m venv .venv
.venv/bin/pip install paho-mqtt pillow pytest minimalmodbus pyserial smbus2
.venv/bin/python -m pytest
```

The old `tower-monitor.py` is gitignored (it holds a plaintext password).
