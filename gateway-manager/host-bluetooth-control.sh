#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=${GATEWAY_STATE_DIR:-/var/lib/gateway}
STATE_FILE="$STATE_DIR/host-bluetooth.state"
HOST_NS=(nsenter --target 1 --mount --uts --ipc --net --pid --root=/proc/1/root --wd=/)

mkdir -p "$STATE_DIR"

host_has_systemd() {
  "${HOST_NS[@]}" test -d /run/systemd/system >/dev/null 2>&1
}

host_systemctl() {
  "${HOST_NS[@]}" /bin/systemctl "$@"
}

record_original_state() {
  if [ -f "$STATE_FILE" ]; then
    return 0
  fi

  local active=unknown
  local enabled=unknown
  local masked=no
  if host_has_systemd; then
    active=$(host_systemctl is-active bluetooth.service 2>/dev/null || true)
    enabled=$(host_systemctl is-enabled bluetooth.service 2>/dev/null || true)
    if [ "$enabled" = "masked" ]; then
      masked=yes
    fi
  fi

  umask 077
  {
    printf 'HOST_SERVICE_ACTIVE=%q\n' "$active"
    printf 'HOST_SERVICE_ENABLED=%q\n' "$enabled"
    printf 'HOST_SERVICE_WAS_MASKED=%q\n' "$masked"
  } >"$STATE_FILE"
}

claim_bluetooth() {
  record_original_state

  if host_has_systemd; then
    host_systemctl stop bluetooth.service >/dev/null 2>&1 || true
    host_systemctl disable bluetooth.service >/dev/null 2>&1 || true
    host_systemctl mask bluetooth.service dbus-org.bluez.service >/dev/null 2>&1 || true
  fi

  pkill -TERM -x bluetoothd >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    pgrep -x bluetoothd >/dev/null 2>&1 || break
    sleep 1
  done
  pkill -KILL -x bluetoothd >/dev/null 2>&1 || true

  "${HOST_NS[@]}" /sbin/modprobe bluetooth >/dev/null 2>&1 || true
  "${HOST_NS[@]}" /sbin/modprobe btusb >/dev/null 2>&1 || true
  "${HOST_NS[@]}" /sbin/modprobe rfcomm >/dev/null 2>&1 || true
  rfkill unblock bluetooth >/dev/null 2>&1 || true

  if pgrep -x bluetoothd >/dev/null 2>&1; then
    echo "宿主机 bluetoothd 仍在运行，无法安全接管蓝牙适配器" >&2
    return 1
  fi
}

restore_bluetooth() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "没有已记录的宿主机蓝牙状态"
    return 0
  fi

  # shellcheck disable=SC1090
  source "$STATE_FILE"
  if ! host_has_systemd; then
    echo "宿主机不是 systemd，无法自动恢复蓝牙服务" >&2
    return 1
  fi

  if [ "${HOST_SERVICE_WAS_MASKED:-no}" != "yes" ]; then
    host_systemctl unmask bluetooth.service dbus-org.bluez.service >/dev/null 2>&1 || true
  fi
  case "${HOST_SERVICE_ENABLED:-unknown}" in
    enabled) host_systemctl enable bluetooth.service >/dev/null 2>&1 || true ;;
    disabled) host_systemctl disable bluetooth.service >/dev/null 2>&1 || true ;;
  esac
  if [ "${HOST_SERVICE_ACTIVE:-unknown}" = "active" ]; then
    host_systemctl start bluetooth.service
  fi
  rm -f "$STATE_FILE"
}

show_status() {
  local manager=unknown
  local active=unknown
  local enabled=unknown
  if host_has_systemd; then
    manager=systemd
    active=$(host_systemctl is-active bluetooth.service 2>/dev/null || true)
    enabled=$(host_systemctl is-enabled bluetooth.service 2>/dev/null || true)
  fi
  printf '{"manager":"%s","service_active":"%s","service_enabled":"%s","handoff_saved":%s}\n' \
    "$manager" "$active" "$enabled" "$([ -f "$STATE_FILE" ] && printf true || printf false)"
}

case "${1:-}" in
  claim) claim_bluetooth ;;
  restore) restore_bluetooth ;;
  status) show_status ;;
  *) echo "用法: $0 {claim|restore|status}" >&2; exit 2 ;;
esac
