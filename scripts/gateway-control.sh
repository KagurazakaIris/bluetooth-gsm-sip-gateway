#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=${GATEWAY_STATE_DIR:-/var/lib/gateway}
BT_PID_FILE="$STATE_DIR/bluetoothd.pid"
AGENT_PID_FILE="$STATE_DIR/bt-agent.pid"
HOST_CONTROL=/opt/gateway/scripts/host-bluetooth-control.sh

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

asterisk_ready() {
  /usr/sbin/asterisk -rx "core waitfullybooted" >/dev/null 2>&1
}

stop_internal_bluetooth() {
  if asterisk_ready; then
    /usr/sbin/asterisk -rx "module unload chan_mobile.so" >/dev/null 2>&1 || true
  fi
  stop_pid_file "$AGENT_PID_FILE" bt-agent
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

  bt-agent -c NoInputNoOutput >"$STATE_DIR/bt-agent.log" 2>&1 &
  echo $! >"$AGENT_PID_FILE"
  bluetoothctl power on >/dev/null 2>&1 || true
  bluetoothctl pairable off >/dev/null 2>&1 || true
  bluetoothctl discoverable off >/dev/null 2>&1 || true
}

reload_chan_mobile() {
  if [ ! -f /etc/asterisk/chan_mobile.conf ] || ! grep -q '^\[mobile_phone\]$' /etc/asterisk/chan_mobile.conf; then
    echo "尚未生成手机配置，跳过 chan_mobile 加载"
    return 0
  fi
  if ! asterisk_ready; then
    echo "Asterisk 尚未就绪，配置将在启动后加载"
    return 0
  fi
  /usr/sbin/asterisk -rx "module unload chan_mobile.so" >/dev/null 2>&1 || true
  /usr/sbin/asterisk -rx "module load chan_mobile.so"
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
  reload_chan_mobile
}

return_to_host() {
  stop_internal_bluetooth
  "$HOST_CONTROL" restore
}

case "${1:-}" in
  start-bluetooth) start_internal_bluetooth ;;
  stop-bluetooth) stop_internal_bluetooth ;;
  repair) repair_environment ;;
  reload-mobile) reload_chan_mobile ;;
  return-to-host) return_to_host ;;
  *) echo "用法: $0 {start-bluetooth|stop-bluetooth|repair|reload-mobile|return-to-host}" >&2; exit 2 ;;
esac
