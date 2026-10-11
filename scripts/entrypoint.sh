#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=${GATEWAY_STATE_DIR:-/var/lib/gateway}
CONTROL=/opt/gateway/scripts/gateway-control.sh
WEB_PID=""
ASTERISK_PID=""
MONITOR_PID=""

mkdir -p "$STATE_DIR" /var/run/dbus /var/run/asterisk /var/lib/bluetooth
chown asterisk:asterisk /var/run/asterisk

cleanup() {
  if [ -n "$WEB_PID" ]; then
    kill "$WEB_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "$ASTERISK_PID" ]; then
    kill "$ASTERISK_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "$MONITOR_PID" ]; then
    kill "$MONITOR_PID" >/dev/null 2>&1 || true
  fi
  if [ "${GATEWAY_RESTORE_HOST_ON_STOP:-1}" = "1" ]; then
    "$CONTROL" return-to-host >/dev/null 2>&1 || true
  else
    "$CONTROL" stop-bluetooth >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT TERM INT

wait_for_hci() {
  local attempts=${1:-30}
  for ((i = 1; i <= attempts; i++)); do
    if hciconfig -a 2>/dev/null | grep -q '^hci'; then
      return 0
    fi
    echo "[*] 等待蓝牙适配器 (${i}/${attempts})..."
    sleep 2
  done
  return 1
}

wait_for_asterisk() {
  for ((i = 1; i <= 30; i++)); do
    if /usr/sbin/asterisk -rx "core waitfullybooted" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

configured_phone_mac() {
  awk -F= '
    /^\[mobile_phone\]$/ { section=1; next }
    /^\[/ { section=0 }
    section && $1 ~ /^[[:space:]]*address[[:space:]]*$/ {
      value=$2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", value); print value; exit
    }
  ' /etc/asterisk/chan_mobile.conf 2>/dev/null || true
}

monitor_gateway() {
  while true; do
    sleep 15
    local phone
    phone=$(configured_phone_mac)
    if [ -n "$phone" ]; then
      bluetoothctl trust "$phone" >/dev/null 2>&1 || true
      bluetoothctl connect "$phone" >/dev/null 2>&1 || true
      if ! /usr/sbin/asterisk -rx "module show like chan_mobile.so" 2>/dev/null | grep -q 'Running'; then
        "$CONTROL" reload-mobile >/dev/null 2>&1 || true
      fi
    fi
    if bluetoothctl show 2>/dev/null | grep -q 'Discovering: yes' && \
       [ -n "$phone" ] && bluetoothctl info "$phone" 2>/dev/null | grep -q 'Connected: yes'; then
      bluetoothctl scan off >/dev/null 2>&1 || true
    fi
  done
}

echo "[*] 准备 Asterisk 模块加载策略..."
if ! grep -Eq '^[[:space:]]*noload[[:space:]]*=>[[:space:]]*chan_mobile\.so' /etc/asterisk/modules.conf; then
  sed -i '/^\[modules\]$/a noload => chan_mobile.so' /etc/asterisk/modules.conf
fi

echo "[*] 启动容器内 D-Bus..."
rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket
dbus-daemon --system --fork

echo "[*] 自动接管并修复蓝牙环境..."
if ! "$CONTROL" repair; then
  echo "[!] 首次蓝牙修复失败，Web 管理页仍将启动，可使用“一键修复蓝牙环境”重试。"
fi

if ! wait_for_hci 5; then
  echo "[!] 当前没有可用蓝牙适配器，继续启动诊断页面。"
fi

PYTHONPATH=/opt/gateway/app python3 -c \
  'from gateway_manager import regenerate_saved_config; regenerate_saved_config()' || true

echo "[*] 启动 Web 管理服务..."
python3 /opt/gateway/app/gateway_manager.py &
WEB_PID=$!

echo "[*] 启动 Asterisk..."
/usr/sbin/asterisk -f -vvv &
ASTERISK_PID=$!
if ! wait_for_asterisk; then
  echo "[!] Asterisk 未能在规定时间内就绪。"
  exit 1
fi

"$CONTROL" reload-mobile || true
monitor_gateway &
MONITOR_PID=$!

# 任一关键进程退出都终止容器，交由 Compose 的重启策略恢复完整服务。
set +e
wait -n
EXIT_STATUS=$?
set -e

if ! kill -0 "$WEB_PID" >/dev/null 2>&1; then
  echo "[!] Web 管理服务意外退出。"
elif ! kill -0 "$ASTERISK_PID" >/dev/null 2>&1; then
  echo "[!] Asterisk 意外退出。"
else
  echo "[!] 网关自愈监控意外退出。"
fi
exit "$EXIT_STATUS"
