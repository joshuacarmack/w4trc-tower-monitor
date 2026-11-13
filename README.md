# W4TRC Tower Monitor

Raspberry Pi 4 + Sixfab 4G HAT at the tower shack. Publishes to MQTT with
Home Assistant discovery; HA does dashboards and notifications.

| Group | What | Source |
|---|---|---|
| Environment | Shack temperature, humidity, dew point (+ pressure on BME280) | SHT41 or BME280 on I2C |
| Mains | Mains power present / lost | Opto-isolated AC detection module → GPIO |
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

Measured on cellular (Nov 2026, apt timers already masked): a flat
**~15 MiB/day (~450 MiB/month)**, almost none of it ours. MQTT to the broker
was ~50 KB in a day. The rest is Tailscale:

| Traffic | Share |
|---|---|
| `log.tailscale.com` (tailscaled's own log upload) | ~30–40% |
| DERP relay + WireGuard/STUN keepalives | most of the rest |
| Control plane (`controlplane.tailscale.com`) | small |

T-Mobile's CGNAT changes the source port per destination
(`tailscale netcheck` → `MappingVariesByDestIP: true`), so peers can't
connect directly and everything goes through a DERP relay.

Photos are ≈ 40 KB each, capped by `max_sent_per_day` (default 3). Watch the
*Cellular data this month* entity, or `vnstat -i wwan0 -d` on the Pi.

Run `sudo ./scripts/cellular-data-saver.sh` on the Pi. It stops apt and other
timers from downloading over cellular, and adds `--no-logs-no-support` to
tailscaled, which turns off the log upload.

## Wiring

Pi header pins (physical numbering). The Sixfab 3G/4G & LTE Base HAT
uses GPIO27 (user LED, driven as an output by the HAT's ID EEPROM) and
GPIO22 (user button), so mains is on GPIO23. `pinctrl get` shows what each
pin is doing.

| Signal | Pi pin | Notes |
|---|---|---|
| I2C 3.3 V | 1 | SHT41 / BME280 VIN |
| I2C SDA (GPIO2) | 3 | |
| I2C SCL (GPIO3) | 5 | |
| GND | 6, 9, 14 | |
| Door (GPIO17) | 11 | Reed switch between pin 11 and GND. Closed door = LOW |
| Mains (GPIO23) | 16 | AC module OUT → pin 16, GND → pin 14, VCC → pin 1 (3.3 V) |

* **Temp sensor:** keep the I2C cable short (< 1 m) and mount the sensor
  away from the Pi and the converter, which run warm.
* **Mains detect:** "110V/220V AC voltage detection" module (opto-isolated,
  "output level 3–8 V"). Mains goes only to its AC screw terminals; the
  optocoupler isolates it from the Pi. **Power the module from 3.3 V
  (pin 1), never 5 V:** OUT swings to VCC, and 5 V on a GPIO can kill the
  Pi. Put it in a closed box with strain relief and a fused cord, and make
  the AC connections unplugged. Most of these pull OUT low when AC is
  present (`present_when_low = true`); some batches are inverted, so check
  with the cord in and out and flip the setting if needed. The Pi itself
  must run from the battery (it does, via the 12 V → 5 V converter),
  otherwise it can't report a mains failure.
* **Renogy Rover:** USB-RS232 adapter (genuine FTDI) → DB9 → RJ12 into the
  Rover's RS232 port. Some RJ12 pins on the Rover carry supply voltage:
  **meter the port and confirm TX/RX/GND against Renogy's pinout before
  connecting.** The modem takes `/dev/ttyUSB0`–`4`, so the adapter lands on
  `ttyUSB5` or later: put its `/dev/serial/by-id/...` path in the config
  (there is no default port).
* **Power:** the LTE modem pulls short bursts of ~2 A when it powers up and
  attaches. On the bench the Pi logged `Undervoltage detected!` for ~2 s,
  12 s into boot, as the modem came up. At the tower use a 12 V→5 V
  converter rated **5 V / 3 A or more**, with short, thick leads. The
  *Pi undervoltage* entities show whether it's holding up.
* **Camera:** any UVC USB webcam with IR LEDs and auto IR-cut. Night shots
  come out black and white.

## Network setup (Tailscale)

The broker (EMQX, `192.168.4.34`) is reached through the tailnet's subnet
router (JC-UTIL02 advertises `192.168.4.0/24`), so the same address works on
the bench and over cellular:

```sh
sudo tailscale set --accept-routes=true
```

**DNS:** with `--accept-dns` on and no global nameservers in the tailnet,
Tailscale's resolver fell back to a dead upstream and *every* lookup failed
(the old script never resolved its broker). The Pi uses fixed resolvers
instead, and the config uses an IP so MQTT doesn't need DNS at all:

```sh
sudo tailscale set --accept-dns=false
printf '[main]\ndns=none\n' | sudo tee /etc/NetworkManager/conf.d/90-static-dns.conf
sudo rm -f /etc/resolv.conf
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 9.9.9.9\n' | sudo tee /etc/resolv.conf
sudo systemctl reload NetworkManager
```

## Cellular

The HAT's Telit LE910C4 is run by ModemManager over QMI (`cdc-wdm0` /
`wwan0`), with a NetworkManager `gsm` connection called `tower-cellular`.
The Sixfab PPP scripts in `/opt/sixfab` are not used (no PPP service is
enabled), so nothing fights ModemManager for the modem. On the bench
Ethernet wins (route metric 100 vs 700); unplug it and traffic moves to
`wwan0`.

By default NetworkManager gives up after 4 failed attempts, which would
leave an unattended site offline after a long cell outage. Retry forever
instead:

```sh
sudo nmcli con mod tower-cellular connection.autoconnect-retries 0
```

## Bench bring-up

1. Flash Raspberry Pi OS Lite (64-bit). Re-install Sixfab and Tailscale.
2. Clone this repo onto the Pi and run `sudo ./scripts/install.sh`.
3. Edit `/etc/tower-monitor/config.toml` (MQTT host, user, password). Set
   `enabled = false` for anything not wired yet.
4. Check hardware one piece at a time:
   * `i2cdetect -y 1` → `44` (SHT4x) or `76`/`77` (BME280)
   * `ls /dev/serial/by-id/` → the FTDI adapter
   * `fswebcam -d /dev/video0 -r 1280x720 -S 30 test.jpg`
   * `mmcli -L` → `[Telit] LE910C4-NF`; `ip -br addr` → `wwan0` has an address
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
