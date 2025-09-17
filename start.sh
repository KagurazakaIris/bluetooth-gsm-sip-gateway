#!/usr/bin/env bash
set -euo pipefail

echo "[*] Pre-flight checks..."
mkdir -p /var/run/dbus /var/lib/bluetooth /var/run/asterisk
chown asterisk:asterisk /var/run/asterisk

# --- DBUS: private system bus inside the container ---
echo "[*] Starting private system dbus in container..."
# Clean up possible stale files from previous run
rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket || true
dbus-daemon --system --fork
sleep 1

# Quick check
if [ ! -S /var/run/dbus/system_bus_socket ]; then
  echo "[!] dbus system socket missing after start"; ls -l /var/run/dbus || true
  exit 1
fi

# --- Bluetooth daemon ---
echo "[*] Starting bluetoothd (BlueZ) ..."
rm -f /run/sdp || true
/usr/sbin/bluetoothd --compat -n -d &
BT_PID=$!
sleep 2

if ! ps -p "$BT_PID" >/dev/null 2>&1; then
  echo "[!] bluetoothd appears down. Diagnostics:"
  echo "    - rfkill list (if present)"; rfkill list || true
  echo "    - hciconfig -a"; hciconfig -a || true
  echo "    - dmesg | tail -n 100"; dmesg | tail -n 100 || true
  exit 1
fi

# --- Asterisk ---
echo "[*] Launching Asterisk (foreground)..."
exec /usr/sbin/asterisk -f -vvv
