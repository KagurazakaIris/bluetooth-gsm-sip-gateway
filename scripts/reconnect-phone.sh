#!/usr/bin/env bash
set -euo pipefail

phone=${1:-}
[[ $phone =~ ^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$ ]] || exit 0

# 未完成配对时不能自动信任或连接，否则手机侧会反复收到无代理的密钥确认请求。
if bluetoothctl paired-devices | awk -v mac="$phone" '$1 == "Device" && toupper($2) == toupper(mac) { found=1 } END { exit !found }'; then
  printf 'trust %s\nconnect %s\nquit\n' "$phone" "$phone" | timeout 15 bluetoothctl >/dev/null 2>&1 || true
fi
