#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=${GATEWAY_STATE_DIR:-/var/lib/gateway}
export PYTHONPATH=/opt/gateway
CONTROL=/opt/gateway/scripts/gateway-control.sh
WEB_PID=""
ASTERISK_PID=""
BARESIP_PID=""
BRIDGE_PID=""
MONITOR_PID=""
LOOPBACK_OWNED=0

mkdir -p "$STATE_DIR" /var/run/dbus /var/run/asterisk /var/lib/bluetooth /var/spool/asterisk/monitor
chown asterisk:asterisk /var/run/asterisk /var/spool/asterisk/monitor

cleanup() {
  trap - EXIT TERM INT
  for pid in "$MONITOR_PID" "$BRIDGE_PID" "$BARESIP_PID" "$ASTERISK_PID" "$WEB_PID"; do
    if [ -n "$pid" ]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in "$BRIDGE_PID" "$BARESIP_PID" "$ASTERISK_PID"; do
    if [ -n "$pid" ]; then
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  if [ "${GATEWAY_RESTORE_HOST_ON_STOP:-1}" = "1" ]; then
    "$CONTROL" return-to-host >/dev/null 2>&1 || true
  else
    "$CONTROL" stop-bluetooth >/dev/null 2>&1 || true
  fi
  if [ "$LOOPBACK_OWNED" -eq 1 ]; then
    modprobe -r snd-aloop >/dev/null 2>&1 || echo '[!] ALSA Loopback 仍被使用，保留内核模块。'
  fi
}
trap cleanup EXIT TERM INT

wait_for_asterisk() {
  for ((i = 1; i <= 30; i++)); do
    if /usr/sbin/asterisk -rx "core waitfullybooted" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

alive_file() {
  local file=$1
  [ -s "$file" ] && kill -0 "$(cat "$file")" >/dev/null 2>&1
}

monitor_gateway() {
  local bluetooth_repaired=0
  while sleep 15; do
    for service in bluetoothd ofonod bluealsa; do
      if ! alive_file "$STATE_DIR/$service.pid"; then
        if [ "$bluetooth_repaired" -eq 0 ]; then
          echo "[!] $service 已退出，尝试原地修复 HFP 环境。"
          if "$CONTROL" repair; then
            bluetooth_repaired=1
            continue 2
          fi
        fi
        echo "[!] $service 修复失败或再次退出。"
        return 1
      fi
    done
    local phone
    phone=$(python3 -c 'import json,sys; from pathlib import Path; p=Path(sys.argv[1]); print(json.loads(p.read_text()).get("phone_mac", "") if p.exists() else "")' "$STATE_DIR/settings.json" 2>/dev/null || true)
    if [ -n "$phone" ]; then
      printf 'trust %s\nconnect %s\nquit\n' "$phone" "$phone" | timeout 15 bluetoothctl >/dev/null 2>&1 || true
    fi
  done
}

if ! grep -q '^snd_aloop ' /proc/modules; then
  modprobe snd-aloop
  LOOPBACK_OWNED=1
fi
if ! grep -q '^snd_aloop ' /proc/modules; then
  echo '[!] 无法加载 ALSA Loopback，不能启动双向通话。' >&2
  exit 1
fi

rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket
dbus-daemon --system --fork
if ! "$CONTROL" repair; then
  echo '[!] 蓝牙启动失败，Web 诊断页仍将运行。'
fi

PYTHONPATH=/opt/gateway python3 -c 'from app.gateway_manager import regenerate_saved_config; regenerate_saved_config()'

python3 /opt/gateway/app/gateway_manager.py &
WEB_PID=$!

/usr/sbin/asterisk -f &
ASTERISK_PID=$!
if ! wait_for_asterisk; then
  echo '[!] Asterisk 未能就绪。' >&2
  exit 1
fi

baresip -f "$STATE_DIR/baresip" </dev/null &
BARESIP_PID=$!
PYTHONPATH=/opt/gateway python3 /opt/gateway/app/hfp_bridge.py &
BRIDGE_PID=$!
echo "$BRIDGE_PID" >"$STATE_DIR/hfp_bridge.pid"
monitor_gateway &
MONITOR_PID=$!

set +e
wait -n "$WEB_PID" "$ASTERISK_PID" "$BARESIP_PID" "$BRIDGE_PID" "$MONITOR_PID"
status=$?
set -e
echo "[!] 网关关键进程退出，状态码 $status。"
exit "${status:-1}"
