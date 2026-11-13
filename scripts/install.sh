#!/usr/bin/env bash
# Install or update the tower monitor on Raspberry Pi OS (Bookworm or later).
# Run from a checkout of this repo:  sudo ./scripts/install.sh
# Run it on the bench over Wi-Fi/Ethernet: it downloads packages.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/tower-monitor
ETC=/etc/tower-monitor
USER_NAME=towermon

echo "==> Packages"
apt-get update
apt-get install -y \
    python3-venv python3-gpiozero python3-lgpio python3-pil python3-serial python3-smbus2 \
    i2c-tools fswebcam vnstat

echo "==> vnstat: count the cellular interface (it may not exist until the modem is up)"
vnstat --add -i wwan0 >/dev/null 2>&1 || true
systemctl restart vnstat || true

echo "==> Cellular: never stop retrying (default gives up after 4 failures)"
if nmcli -t -f NAME,TYPE con show 2>/dev/null | grep -q ':gsm$'; then
    nmcli -t -f NAME,TYPE con show | awk -F: '$2=="gsm"{print $1}' | while read -r c; do
        nmcli con mod "$c" connection.autoconnect-retries 0
    done
fi

echo "==> Enable I2C"
if command -v raspi-config >/dev/null; then
    raspi-config nonint do_i2c 0
fi

echo "==> Service user"
if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi
for g in gpio i2c dialout video; do
    getent group "$g" >/dev/null && usermod -aG "$g" "$USER_NAME"
done

echo "==> Code -> $APP"
install -d "$APP"
rm -rf "$APP/tower_monitor"
cp -r "$SRC/tower_monitor" "$SRC/requirements.txt" "$APP/"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +
if [[ ! -x "$APP/venv/bin/python" ]]; then
    python3 -m venv --system-site-packages "$APP/venv"
fi
"$APP/venv/bin/pip" install --quiet --upgrade -r "$APP/requirements.txt"

echo "==> Config -> $ETC"
install -d -m 750 -o root -g "$USER_NAME" "$ETC"
if [[ ! -f "$ETC/config.toml" ]]; then
    install -m 640 -o root -g "$USER_NAME" "$SRC/config.example.toml" "$ETC/config.toml"
    echo "    Created $ETC/config.toml -- edit it (MQTT host/user/password) before starting."
    NEW_CONFIG=1
fi

echo "==> systemd"
install -m 644 "$SRC/systemd/tower-monitor.service" /etc/systemd/system/
install -m 755 "$SRC/scripts/tower-tailscale" /usr/local/sbin/tower-tailscale
for u in tower-tailscale-request.path tower-tailscale-request.service \
         tower-tailscale-window.timer tower-tailscale-window.service; do
    install -m 644 "$SRC/systemd/$u" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable tower-monitor.service
# HA "Tailscale on/off" buttons. Harmless while tailscaled is always on:
# tower-tailscale only stops it once cellular-data-saver.sh --tailscale-on-demand
# has enabled the window timer.
systemctl enable --now tower-tailscale-request.path

if [[ "${NEW_CONFIG:-0}" == 1 ]]; then
    echo
    echo "Next: sudo nano $ETC/config.toml   then   sudo systemctl start tower-monitor"
else
    systemctl restart tower-monitor.service
    echo "Restarted. Logs: journalctl -u tower-monitor -f"
fi
