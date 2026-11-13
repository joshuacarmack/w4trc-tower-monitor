#!/usr/bin/env bash
# Stop background jobs that would quietly eat the 100 MB/month data plan.
# Safe to re-run. Undo with:  sudo ./scripts/cellular-data-saver.sh --undo
# Update the Pi by hand, ideally on the bench:  sudo apt update && sudo apt upgrade
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

UNITS=(
    apt-daily.timer
    apt-daily-upgrade.timer
    man-db.timer
    e2scrub_all.timer
    unattended-upgrades.service
    packagekit.service
    rpi-connect.service
)

# tailscaled uploads its own logs to log.tailscale.com. On the tower Pi that
# was ~30-40% of all cellular traffic (~5 MB/day). The flag stops it; the only
# cost is that Tailscale support can't see this node's logs.
TS_DEFAULTS=/etc/default/tailscaled
TS_FLAG=--no-logs-no-support

if [[ "${1:-}" == "--undo" ]]; then
    for u in "${UNITS[@]}"; do
        systemctl unmask "$u" 2>/dev/null || true
        systemctl enable --now "$u" 2>/dev/null || true
    done
    rm -f /etc/systemd/journald.conf.d/tower-monitor.conf
    systemctl restart systemd-journald
    if grep -q -- "$TS_FLAG" "$TS_DEFAULTS" 2>/dev/null; then
        sed -i "s/ *$TS_FLAG//" "$TS_DEFAULTS"
        systemctl restart tailscaled
    fi
    echo "Background jobs restored."
    exit 0
fi

for u in "${UNITS[@]}"; do
    if systemctl list-unit-files "$u" >/dev/null 2>&1; then
        systemctl disable --now "$u" 2>/dev/null || true
        systemctl mask "$u" 2>/dev/null || true
        echo "disabled $u"
    fi
done

# Cap the journal so the SD card doesn't fill over months unattended.
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/tower-monitor.conf <<'EOF'
[Journal]
SystemMaxUse=100M
EOF
systemctl restart systemd-journald

if [[ -f "$TS_DEFAULTS" ]] && ! grep -q -- "$TS_FLAG" "$TS_DEFAULTS"; then
    if grep -q '^FLAGS=' "$TS_DEFAULTS"; then
        sed -i "s/^FLAGS=\"\(.*\)\"/FLAGS=\"\1 $TS_FLAG\"/; s/^FLAGS=\" /FLAGS=\"/" "$TS_DEFAULTS"
    else
        echo "FLAGS=\"$TS_FLAG\"" >> "$TS_DEFAULTS"
    fi
    echo "tailscaled: $TS_FLAG (restarting; an SSH session over Tailscale will drop briefly)"
    systemctl restart tailscaled
fi

echo
echo "Done. Also check:"
echo "  - Sixfab Core / sixfab agent: if installed it reports telemetry over cellular."
echo "  - vnstat is counting the right interface:  vnstat --iflist"
echo "  - Billing cycle day: set MonthRotate in /etc/vnstat.conf if not the 1st."
