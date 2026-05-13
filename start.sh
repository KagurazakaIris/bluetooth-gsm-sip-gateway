#!/usr/bin/env bash
set -euo pipefail

wait_for_hci() {
  local attempts=${1:-30}
  local delay=${2:-2}
  local i

  for ((i = 1; i <= attempts; i++)); do
    if hciconfig -a 2>/dev/null | grep -q '^hci'; then
      return 0
    fi
    echo "[*] Waiting for Bluetooth adapter (${i}/${attempts})..."
    sleep "$delay"
  done

  return 1
}

wait_for_asterisk_cli() {
  local attempts=${1:-30}
  local delay=${2:-1}
  local i

  for ((i = 1; i <= attempts; i++)); do
    if /usr/sbin/asterisk -rx "core waitfullybooted" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done

  return 1
}

ensure_chan_mobile_manual_load() {
  if ! grep -Eq '^[[:space:]]*noload[[:space:]]*=>[[:space:]]*chan_mobile\.so[[:space:]]*$' /etc/asterisk/modules.conf; then
    sed -i '/^\[modules\]$/a noload => chan_mobile.so' /etc/asterisk/modules.conf
  fi
}

get_chan_mobile_adapter_address() {
  awk -F= '
    /^\[adapter\]$/ { in_section=1; next }
    /^\[/ { in_section=0 }
    in_section {
      key=$1
      value=$2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (key == "address") {
        print value
        exit
      }
    }
  ' /etc/asterisk/chan_mobile.conf
}

get_chan_mobile_device_address() {
  awk -F= '
    /^\[/ {
      section=$0
      gsub(/^\[|\]$/, "", section)
      next
    }
    section != "" && section != "general" && section != "adapter" {
      key=$1
      value=$2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (key == "address") {
        print value
        exit
      }
    }
  ' /etc/asterisk/chan_mobile.conf
}

get_phone_bond_info_path() {
  local adapter_addr
  local phone_addr

  adapter_addr=$(get_chan_mobile_adapter_address)
  phone_addr=$(get_chan_mobile_device_address)

  if [ -z "${adapter_addr:-}" ] || [ -z "${phone_addr:-}" ]; then
    echo "[!] Unable to determine adapter/device addresses from chan_mobile.conf." >&2
    return 1
  fi

  printf '/var/lib/bluetooth/%s/%s/info
' "$adapter_addr" "$phone_addr"
}

has_persistent_phone_bond() {
  local bond_info_path

  bond_info_path=$(get_phone_bond_info_path) || return 1
  [ -f "$bond_info_path" ]
}

require_persistent_phone_bond() {
  local bond_info_path

  bond_info_path=$(get_phone_bond_info_path) || return 1

  if [ -f "$bond_info_path" ]; then
    return 0
  fi

  echo "[!] No persistent host Bluetooth bond found for the configured phone."
  echo "    Pair and trust the phone on the host first, then restart the container."
  echo "    Expected bond file: $bond_info_path"
  return 1
}

is_phone_connected() {
  local phone_addr

  phone_addr=$(get_chan_mobile_device_address)
  if [ -z "${phone_addr:-}" ]; then
    return 1
  fi

  bluetoothctl info "$phone_addr" 2>/dev/null | grep -q 'Connected: yes'
}

ensure_phone_trusted() {
  local phone_addr

  phone_addr=$(get_chan_mobile_device_address)
  if [ -z "${phone_addr:-}" ]; then
    return 1
  fi

  if bluetoothctl info "$phone_addr" 2>/dev/null | grep -q 'Trusted: yes'; then
    return 0
  fi

  echo "[*] Marking configured phone as trusted in container Bluetooth state..."
  printf 'trust %s
quit
' "$phone_addr" | bluetoothctl >/dev/null 2>&1 || true
  bluetoothctl info "$phone_addr" 2>/dev/null | grep -q 'Trusted: yes'
}

attempt_phone_connection() {
  local phone_addr

  phone_addr=$(get_chan_mobile_device_address)
  if [ -z "${phone_addr:-}" ]; then
    return 1
  fi

  if is_phone_connected; then
    return 0
  fi

  echo "[*] Attempting Bluetooth connection to $phone_addr..."
  printf 'connect %s
quit
' "$phone_addr" | bluetoothctl >/dev/null 2>&1 || true
  sleep 2

  if is_phone_connected; then
    echo "[*] Phone Bluetooth link is up."
    return 0
  fi

  return 1
}

wait_for_phone_connection() {
  local attempts=${1:-10}
  local delay=${2:-3}
  local i

  for ((i = 1; i <= attempts; i++)); do
    if attempt_phone_connection; then
      return 0
    fi
    echo "[*] Waiting for phone connection (${i}/${attempts})..."
    sleep "$delay"
  done

  return 1
}

is_adapter_discovering() {
  bluetoothctl show 2>/dev/null | grep -q 'Discovering: yes'
}

reconcile_bluetooth_discovery() {
  if is_phone_connected && is_adapter_discovering; then
    echo "[*] Phone is connected; stopping Bluetooth discovery loop."
    printf 'scan off
quit
' | bluetoothctl >/dev/null 2>&1 || true
  fi
}

ensure_chan_mobile_running() {
  local module_status

  module_status=$(/usr/sbin/asterisk -rx "module show like chan_mobile.so" 2>/dev/null || true)
  if grep -Eq 'chan_mobile\.so[[:space:]].*[[:space:]]Running[[:space:]]+extended' <<<"$module_status"; then
    return 0
  fi

  if ! hciconfig -a 2>/dev/null | grep -q '^hci'; then
    echo "[*] Skipping chan_mobile recovery: no Bluetooth adapter present yet."
    return 1
  fi

  if ! has_persistent_phone_bond; then
    echo "[*] Skipping chan_mobile recovery: no persistent BlueZ bond info for configured phone yet."
    return 1
  fi

  ensure_phone_trusted || true
  attempt_phone_connection || true

  echo "[*] Attempting chan_mobile recovery..."
  /usr/sbin/asterisk -rx "module unload chan_mobile.so" >/dev/null 2>&1 || true
  /usr/sbin/asterisk -rx "module load chan_mobile.so" >/dev/null 2>&1 || true

  module_status=$(/usr/sbin/asterisk -rx "module show like chan_mobile.so" 2>/dev/null || true)
  if grep -Eq 'chan_mobile\.so[[:space:]].*[[:space:]]Running[[:space:]]+extended' <<<"$module_status"; then
    echo "[*] chan_mobile is running."
    return 0
  fi

  echo "[!] chan_mobile recovery attempt did not reach Running state."
  return 1
}

monitor_chan_mobile() {
  while true; do
    sleep 15
    ensure_phone_trusted || true
    attempt_phone_connection || true
    ensure_chan_mobile_running || true
    reconcile_bluetooth_discovery || true
  done
}

echo "[*] Pre-flight checks..."
mkdir -p /var/run/dbus /var/lib/bluetooth /var/run/asterisk
chown asterisk:asterisk /var/run/asterisk
rfkill unblock bluetooth >/dev/null 2>&1 || true
ensure_chan_mobile_manual_load

# --- DBUS: private system bus inside the container ---
echo "[*] Starting private system dbus in container..."
rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket || true
dbus-daemon --system --fork
sleep 1

if [ ! -S /var/run/dbus/system_bus_socket ]; then
  echo "[!] dbus system socket missing after start"
  ls -l /var/run/dbus || true
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
  echo "    - rfkill list (if present)"
  rfkill list || true
  echo "    - hciconfig -a"
  hciconfig -a || true
  echo "    - dmesg | tail -n 100"
  dmesg | tail -n 100 || true
  exit 1
fi

echo "[*] Waiting for Bluetooth adapter..."
if ! wait_for_hci 30 2; then
  echo "[!] No Bluetooth adapter became available in time. Diagnostics:"
  echo "    - rfkill list (if present)"
  rfkill list || true
  echo "    - hciconfig -a"
  hciconfig -a || true
  echo "    - dmesg | tail -n 100"
  dmesg | tail -n 100 || true
  exit 1
fi

if ! require_persistent_phone_bond; then
  exit 1
fi

echo "[*] Applying conservative Bluetooth policy (non-discoverable, non-pairable)..."
printf 'power on
pairable off
discoverable off
quit
' | bluetoothctl >/dev/null 2>&1 || true
ensure_phone_trusted || true

if ! wait_for_phone_connection 10 3; then
  echo "[*] Phone is not connected yet; continuing startup and keeping automatic retries enabled."
fi

# --- Asterisk ---
echo "[*] Launching Asterisk..."
/usr/sbin/asterisk -f -vvv &
ASTERISK_PID=$!

if ! wait_for_asterisk_cli 30 1; then
  echo "[!] Asterisk CLI did not become ready in time."
  kill "$ASTERISK_PID" >/dev/null 2>&1 || true
  wait "$ASTERISK_PID" >/dev/null 2>&1 || true
  exit 1
fi

attempt_phone_connection || true
ensure_chan_mobile_running || true
reconcile_bluetooth_discovery || true
monitor_chan_mobile &

wait "$ASTERISK_PID"
