#!/usr/bin/env python3
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse


STATE_DIR = Path(os.environ.get("GATEWAY_STATE_DIR", "/var/lib/gateway"))
SETTINGS_FILE = STATE_DIR / "settings.json"
WEB_ROOT = Path("/opt/gateway/app/web")
CONTROL = "/opt/gateway/scripts/gateway-control.sh"
HOST_CONTROL = "/opt/gateway/scripts/host-bluetooth-control.sh"
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
SIP_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,31}$")


class OperationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.kind = "none"
        self.message = ""
        self.error = ""
        self.devices = []

    def begin(self, kind, message):
        with self.lock:
            if self.kind != "none":
                return False
            self.kind = kind
            self.message = message
            self.error = ""
            return True

    def finish(self, message="", error=""):
        with self.lock:
            self.kind = "none"
            self.message = message
            self.error = error

    def snapshot(self):
        with self.lock:
            return {
                "kind": self.kind,
                "message": self.message,
                "error": self.error,
                "devices": list(self.devices),
            }


operations = OperationState()
post_lock = threading.Lock()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run(command, timeout=30, check=True):
    result = subprocess.run(
        command,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    output = result.stdout.strip()
    if check and result.returncode != 0:
        raise RuntimeError(output or "命令执行失败: " + " ".join(command))
    return output


def normalize_mac(value):
    mac = str(value or "").strip().upper()
    if not MAC_RE.fullmatch(mac):
        raise ValueError("蓝牙 MAC 地址无效")
    return mac


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, SETTINGS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bluetooth_show():
    output = run(["bluetoothctl", "show"], timeout=8, check=False)
    address = ""
    powered = False
    discovering = False
    pairable = False
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Controller "):
            parts = text.split()
            if len(parts) >= 2:
                address = parts[1].upper()
        elif text == "Powered: yes":
            powered = True
        elif text == "Discovering: yes":
            discovering = True
        elif text == "Pairable: yes":
            pairable = True
    return {
        "address": address,
        "powered": powered,
        "discovering": discovering,
        "pairable": pairable,
    }


def device_info(mac):
    output = run(["bluetoothctl", "info", mac], timeout=8, check=False)
    result = {
        "mac": mac,
        "name": "未知设备",
        "paired": False,
        "trusted": False,
        "connected": False,
        "rssi": None,
    }
    fields = {
        "Name": "name",
        "Alias": "name",
        "Paired": "paired",
        "Trusted": "trusted",
        "Connected": "connected",
        "RSSI": "rssi",
    }
    for line in output.splitlines():
        text = line.strip()
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        if key not in fields:
            continue
        target = fields[key]
        value = value.strip()
        if target in ("paired", "trusted", "connected"):
            result[target] = value == "yes"
        elif target == "rssi":
            try:
                result[target] = int(value)
            except ValueError:
                pass
        elif value:
            result[target] = value
    return result


def discovered_devices():
    output = run(["bluetoothctl", "devices"], timeout=8, check=False)
    addresses = []
    for line in output.splitlines():
        match = re.search(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b", line)
        if match:
            mac = match.group(1).upper()
            if mac not in addresses:
                addresses.append(mac)
    return [device_info(mac) for mac in addresses]


def detect_hfp_channel(mac):
    output = run(["sdptool", "browse", mac], timeout=25, check=False)
    in_hfp_gateway = False
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("Service Name:"):
            name = text.split(":", 1)[1].strip().lower()
            in_hfp_gateway = "handsfree" in name and "gateway" in name
            continue
        if in_hfp_gateway:
            match = re.match(r"Channel:\s*(\d+)$", text)
            if match:
                channel = int(match.group(1))
                if 1 <= channel <= 30:
                    return channel, True
    return 3, False


def write_mobile_config(adapter_mac, phone_mac, channel):
    adapter_mac = normalize_mac(adapter_mac)
    phone_mac = normalize_mac(phone_mac)
    if not isinstance(channel, int) or not 1 <= channel <= 30:
        raise ValueError("HFP RFCOMM 通道必须在 1 到 30 之间")
    content = (
        "[general]\n"
        "interval=30\n\n"
        "[adapter]\n"
        "id=usb\n"
        f"address={adapter_mac}\n\n"
        "[mobile_phone]\n"
        f"address={phone_mac}\n"
        f"port={channel}\n"
        "context=from-mobile\n"
        "adapter=usb\n"
    )
    for path in (Path("/etc/asterisk/chan_mobile.conf"), Path("/etc/asterisk/mobile.conf")):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def clear_mobile_config():
    content = Path("/opt/gateway/config/asterisk/chan_mobile.unconfigured.conf").read_text(encoding="utf-8")
    for path in (Path("/etc/asterisk/chan_mobile.conf"), Path("/etc/asterisk/mobile.conf")):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    settings = load_settings()
    for key in ("phone_mac", "hfp_channel", "hfp_channel_detected"):
        settings.pop(key, None)
    save_settings(settings)
    run(["/usr/sbin/asterisk", "-rx", "module unload chan_mobile.so"], timeout=15, check=False)


def regenerate_saved_config():
    settings = load_settings()
    adapter_mac = settings.get("adapter_mac", "")
    phone_mac = settings.get("phone_mac", "")
    channel = settings.get("hfp_channel", 3)
    if MAC_RE.fullmatch(adapter_mac) and MAC_RE.fullmatch(phone_mac):
        write_mobile_config(adapter_mac, phone_mac, int(channel))
    sip_user = settings.get("sip_user", "")
    sip_secret = settings.get("sip_secret", "")
    if SIP_USER_RE.fullmatch(sip_user) and sip_secret:
        write_sip_config(sip_user, sip_secret)
    return True


def write_sip_config(user, secret):
    user = str(user or "").strip()
    secret = str(secret or "")
    if not SIP_USER_RE.fullmatch(user):
        raise ValueError("SIP 分机只能包含字母、数字、点、下划线和连字符")
    if not secret or len(secret) > 128 or "\n" in secret or "\r" in secret:
        raise ValueError("SIP 密码无效")
    sip_content = (
        "[general]\n"
        "udpbindaddr=0.0.0.0:5060\n"
        "transport=udp\n"
        "allowguest=no\n"
        "srvlookup=yes\n"
        "language=en\n"
        "context=from-sip\n\n"
        "disallow=all\n"
        "allow=alaw\n"
        "allow=ulaw\n\n"
        f"[{user}]\n"
        "type=friend\n"
        "host=dynamic\n"
        f"secret={secret}\n"
        "context=from-sip\n"
        f"callerid=\"Softphone\" <{user}>\n"
        "disallow=all\n"
        "allow=alaw\n"
        "allow=ulaw\n"
        "nat=force_rport,comedia\n"
        "qualify=yes\n"
        "directmedia=no\n"
        "dtmfmode=rfc2833\n"
    )
    extensions_content = (
        "[general]\n"
        "static=yes\n"
        "writeprotect=no\n"
        "clearglobalvars=no\n\n"
        "[globals]\n"
        "MOBILE_IFACE=mobile_phone\n\n"
        "[from-sip]\n"
        "exten => _X.,1,NoOp(Outbound via ${MOBILE_IFACE}: ${EXTEN})\n"
        " same => n,Dial(Mobile/${MOBILE_IFACE}/${EXTEN},60)\n"
        " same => n,Hangup()\n\n"
        "[from-mobile]\n"
        "exten => s,1,NoOp(Incoming GSM from ${CALLERID(all)} on ${CHANNEL})\n"
        f" same => n,Dial(SIP/{user},30)\n"
        " same => n,Hangup()\n"
    )
    for path, content in (
        (Path("/etc/asterisk/sip.conf"), sip_content),
        (Path("/etc/asterisk/extensions.conf"), extensions_content),
    ):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def configure_sip(user, submitted_secret):
    settings = load_settings()
    secret = str(submitted_secret or "") or settings.get("sip_secret", "")
    write_sip_config(user, secret)
    settings["sip_user"] = str(user).strip()
    settings["sip_secret"] = secret
    save_settings(settings)
    if asterisk_status()["ready"]:
        run(["/usr/sbin/asterisk", "-rx", "sip reload"], timeout=15)
        run(["/usr/sbin/asterisk", "-rx", "dialplan reload"], timeout=15)


def configure_phone(mac, manual_channel=None):
    adapter = bluetooth_show()
    if not adapter["address"]:
        raise RuntimeError("没有检测到可用蓝牙适配器")
    if manual_channel is None:
        channel, detected = detect_hfp_channel(mac)
    else:
        channel = int(manual_channel)
        detected = False
    write_mobile_config(adapter["address"], mac, channel)
    settings = load_settings()
    settings.update({
        "adapter_mac": adapter["address"],
        "phone_mac": mac,
        "hfp_channel": channel,
        "hfp_channel_detected": detected,
    })
    save_settings(settings)
    run([CONTROL, "reload-mobile"], timeout=30)
    return channel, detected


def async_operation(kind, message, target):
    if not operations.begin(kind, message):
        raise RuntimeError("另一个蓝牙操作正在进行")

    def worker():
        try:
            result = target()
            operations.finish(message=str(result or "操作完成"))
        except Exception as exc:
            operations.finish(error=str(exc))

    threading.Thread(target=worker, daemon=True, name="gateway-" + kind).start()


def start_scan():
    def action():
        run(["bluetoothctl", "power", "on"], timeout=8)
        run(["bluetoothctl", "pairable", "on"], timeout=8)
        run(["bluetoothctl", "scan", "on"], timeout=8)
        time.sleep(10)
        run(["bluetoothctl", "scan", "off"], timeout=8, check=False)
        devices = discovered_devices()
        with operations.lock:
            operations.devices = devices
        run(["bluetoothctl", "pairable", "off"], timeout=8, check=False)
        run(["bluetoothctl", "discoverable", "off"], timeout=8, check=False)
        return f"扫描完成，发现 {len(devices)} 个设备"

    async_operation("scan", "正在扫描蓝牙设备", action)


def start_pair(mac):
    mac = normalize_mac(mac)

    def action():
        run(["bluetoothctl", "power", "on"], timeout=8)
        run(["bluetoothctl", "pairable", "on"], timeout=8)
        run(["bluetoothctl", "discoverable", "on"], timeout=8, check=False)
        try:
            if not device_info(mac)["paired"]:
                output = run(["bluetoothctl", "pair", mac], timeout=50)
                if "Failed" in output:
                    raise RuntimeError(output)
            run(["bluetoothctl", "trust", mac], timeout=10)
            run(["bluetoothctl", "connect", mac], timeout=20, check=False)
            channel, detected = configure_phone(mac)
            return f"配对完成，HFP 通道 {channel}" + ("（自动检测）" if detected else "（默认值，请按需修改）")
        finally:
            run(["bluetoothctl", "pairable", "off"], timeout=8, check=False)
            run(["bluetoothctl", "discoverable", "off"], timeout=8, check=False)

    async_operation("pair", "等待手机确认配对", action)


def asterisk_status():
    module = run(["/usr/sbin/asterisk", "-rx", "module show like chan_mobile.so"], timeout=8, check=False)
    devices = run(["/usr/sbin/asterisk", "-rx", "mobile show devices"], timeout=8, check=False)
    return {
        "ready": "Unable to connect" not in module and bool(module),
        "chan_mobile": "Running" in module,
        "mobile_output": devices[-800:] if devices else "",
    }


def full_status():
    settings = load_settings()
    adapter = bluetooth_show()
    phone_mac = settings.get("phone_mac", "")
    phone = device_info(phone_mac) if MAC_RE.fullmatch(phone_mac) else None
    try:
        host = json.loads(run([HOST_CONTROL, "status"], timeout=8))
    except Exception as exc:
        host = {"manager": "unknown", "error": str(exc)}
    operation = operations.snapshot()
    devices = operation.pop("devices")
    return {
        "adapter": adapter,
        "host": host,
        "phone": phone,
        "settings": {
            "phone_mac": phone_mac,
            "hfp_channel": settings.get("hfp_channel", 3),
            "hfp_channel_detected": settings.get("hfp_channel_detected", False),
            "sip_user": settings.get("sip_user", "1001"),
            "sip_has_secret": bool(settings.get("sip_secret")),
        },
        "devices": devices,
        "asterisk": asterisk_status(),
        "operation": operation,
    }


class Handler(SimpleHTTPRequestHandler):
    server_version = "BluetoothGateway/1.0"

    def log_message(self, format_string, *args):
        print("[web] " + format_string % args, flush=True)

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length > 8192:
            raise ValueError("请求体过大")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 请求无效") from exc
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象")
        return data

    def authorized(self):
        token = os.environ.get("GATEWAY_ADMIN_TOKEN", "")
        if not token:
            return True
        return self.headers.get("Authorization", "") == "Bearer " + token

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            try:
                self.send_json(HTTPStatus.OK, full_status())
            except Exception as exc:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if not post_lock.acquire(blocking=False):
            self.send_json(HTTPStatus.CONFLICT, {"error": "另一个管理请求正在处理"})
            return
        try:
            self.handle_post()
        finally:
            post_lock.release()

    def handle_post(self):
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "管理令牌无效"})
            return
        try:
            data = self.read_json()
            path = urlparse(self.path).path
            asynchronous_paths = {
                "/api/bluetooth/scan",
                "/api/bluetooth/pair",
                "/api/system/repair",
            }
            if path not in asynchronous_paths and operations.snapshot()["kind"] != "none":
                raise RuntimeError("蓝牙管理操作进行中，请稍后重试")
            if path == "/api/bluetooth/scan":
                start_scan()
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True})
            elif path == "/api/bluetooth/pair":
                start_pair(data.get("mac"))
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True})
            elif path == "/api/bluetooth/connect":
                mac = normalize_mac(data.get("mac") or load_settings().get("phone_mac"))
                run(["bluetoothctl", "connect", mac], timeout=20)
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/bluetooth/unpair":
                mac = normalize_mac(data.get("mac") or load_settings().get("phone_mac"))
                run(["bluetoothctl", "remove", mac], timeout=15)
                clear_mobile_config()
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/bluetooth/configure":
                mac = normalize_mac(data.get("mac") or load_settings().get("phone_mac"))
                channel = int(data.get("channel", 3))
                configure_phone(mac, channel)
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/system/repair":
                async_operation("repair", "正在修复蓝牙环境", lambda: run([CONTROL, "repair"], timeout=60))
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True})
            elif path == "/api/sip/configure":
                configure_sip(data.get("user"), data.get("secret"))
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/system/reload":
                output = run([CONTROL, "reload-mobile"], timeout=30)
                self.send_json(HTTPStatus.OK, {"ok": True, "message": output})
            elif path == "/api/system/return":
                output = run([CONTROL, "return-to-host"], timeout=45)
                self.send_json(HTTPStatus.OK, {"ok": True, "message": output})
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WEB_ROOT)
    host = os.environ.get("GATEWAY_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("GATEWAY_WEB_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[*] 网关管理页面监听 http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
