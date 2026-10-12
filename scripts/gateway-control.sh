#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=${GATEWAY_STATE_DIR:-/var/lib/gateway}
BT_PID_FILE="$STATE_DIR/bluetoothd.pid"
OFONO_PID_FILE="$STATE_DIR/ofonod.pid"
BLUEALSA_PID_FILE="$STATE_DIR/bluealsa.pid"
HOST_CONTROL=/opt/gateway/scripts/host-bluetooth-control.sh

bluetoothctl_command() {
  printf '%s\nquit\n' "$*" | timeout 10 bluetoothctl
}

stop_pid_file() {
  local file=$1
  local expected_name=$2
  if [ -f "$file" ]; then
    local pid
    pid=$(cat "$file" 2>/dev/null || true)
    local process_name=""
    if [ -n "$pid" ] && [ -r "/proc/$pid/comm" ]; then
      process_name=$(cat "/proc/$pid/comm" 2>/dev/null || true)
    fi
    if [ "$process_name" = "$expected_name" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" >/dev/null 2>&1 || break
        sleep 1
      done
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$file"
  fi
}

stop_internal_bluetooth() {
  stop_pid_file "$BLUEALSA_PID_FILE" bluealsa
  stop_pid_file "$OFONO_PID_FILE" ofonod
  stop_pid_file "$BT_PID_FILE" bluetoothd
  rm -f /run/sdp
}

start_internal_bluetooth() {
  rm -f /run/sdp
  /usr/sbin/bluetoothd --compat -n >"$STATE_DIR/bluetoothd.log" 2>&1 &
  echo $! >"$BT_PID_FILE"
  sleep 2
  if ! kill -0 "$(cat "$BT_PID_FILE")" >/dev/null 2>&1; then
    echo "容器内 bluetoothd 启动失败" >&2
    tail -n 20 "$STATE_DIR/bluetoothd.log" >&2 || true
    return 1
  fi

  ofonod -n >"$STATE_DIR/ofono.log" 2>&1 &
  echo $! >"$OFONO_PID_FILE"
  sleep 2
  bluealsa -p hfp-ofono >"$STATE_DIR/bluealsa.log" 2>&1 &
  echo $! >"$BLUEALSA_PID_FILE"
  sleep 2
  for file in "$OFONO_PID_FILE" "$BLUEALSA_PID_FILE"; do
    if ! kill -0 "$(cat "$file")" >/dev/null 2>&1; then
      echo "HFP 进程启动失败: $file" >&2
      return 1
    fi
  done
  bluetoothctl_command power on >/dev/null 2>&1 || true
  bluetoothctl_command pairable off >/dev/null 2>&1 || true
  bluetoothctl_command discoverable off >/dev/null 2>&1 || true
}

repair_environment() {
  stop_internal_bluetooth
  "$HOST_CONTROL" claim
  rfkill unblock bluetooth >/dev/null 2>&1 || true
  local adapter_path
  local adapter
  for adapter_path in /sys/class/bluetooth/hci*; do
    [ -e "$adapter_path" ] || continue
    adapter=${adapter_path##*/}
    hciconfig "$adapter" down >/dev/null 2>&1 || true
    sleep 1
    hciconfig "$adapter" up >/dev/null 2>&1 || true
  done
  start_internal_bluetooth
}

return_to_host() {
  stop_internal_bluetooth
  "$HOST_CONTROL" restore
}

case "${1:-}" in
  start-bluetooth) start_internal_bluetooth ;;
  stop-bluetooth) stop_internal_bluetooth ;;
  repair) repair_environment ;;
  return-to-host) return_to_host ;;
  *) echo "用法: $0 {start-bluetooth|stop-bluetooth|repair|return-to-host}" >&2; exit 2 ;;
esac
