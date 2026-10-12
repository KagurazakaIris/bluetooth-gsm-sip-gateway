#!/usr/bin/env python3
import json
import os
import re
import select
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

from app.gateway_config import asterisk_extensions, asterisk_sip, baresip_accounts, baresip_config


STATE_DIR = Path(os.environ.get("GATEWAY_STATE_DIR", "/var/lib/gateway"))
SETTINGS_FILE = STATE_DIR / "settings.json"
WEB_ROOT = Path("/opt/gateway/app/web")
CONTROL = "/opt/gateway/scripts/gateway-control.sh"
HOST_CONTROL = "/opt/gateway/scripts/host-bluetooth-control.sh"
MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
SIP_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,31}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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
bluetoothctl_lock = threading.Lock()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run(command, timeout=30, check=True, input_text=None):
    result = subprocess.run(
        command,
        input=input_text,
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


def clean_bluetoothctl_output(output):
    return ANSI_RE.sub("", output).replace("\r", "")


def bluetoothctl(*arguments, timeout=8, check=True):
    command = " ".join(str(argument) for argument in arguments)
    with bluetoothctl_lock:
        output = run(
            ["bluetoothctl"],
            timeout=timeout,
            check=False,
            input_text=command + "\nquit\n",
        )
    output = clean_bluetoothctl_output(output)
    if check and (
        "Failed" in output
        or "not available" in output
        or "No default controller" in output
    ):
        raise RuntimeError(output)
    return output


def bluetoothctl_pair(mac, timeout=50):
    with bluetoothctl_lock:
        process = subprocess.Popen(
            ["bluetoothctl", "--agent", "NoInputNoOutput"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = bytearray()
        deadline = time.monotonic() + timeout
        discovery_deadline = min(deadline, time.monotonic() + 15)
        pair_started = False
        try:
            process.stdin.write(b"default-agent\nscan on\n")
            process.stdin.flush()
            while time.monotonic() < deadline:
                readable, _, _ = select.select(
                    [process.stdout],
                    [],
                    [],
                    min(0.5, max(0, deadline - time.monotonic())),
                )
                if readable:
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output.extend(chunk)
                    text = clean_bluetoothctl_output(output.decode("utf-8", errors="replace"))
                    if not pair_started and mac in text:
                        process.stdin.write(f"scan off\npair {mac}\n".encode("utf-8"))
                        process.stdin.flush()
                        pair_started = True
                    if "Pairing successful" in text:
                        confirmation_deadline = min(deadline, time.monotonic() + 8)
                        while time.monotonic() < confirmation_deadline:
                            if paired_in_bluez(mac):
                                break
                            time.sleep(0.4)
                        else:
                            raise RuntimeError(f"设备 {mac} 未完成配对，请在手机蓝牙设置中删除旧记录后重试")
                        process.stdin.write(b"quit\n")
                        process.stdin.flush()
                        return text.strip()
                    if "Failed to pair" in text or "not available" in text:
                        raise RuntimeError(text.strip())
                    if not pair_started and "Failed to start discovery" in text:
                        raise RuntimeError(text.strip())
                elif process.poll() is not None:
                    break
                if not pair_started and time.monotonic() >= discovery_deadline:
                    raise RuntimeError(f"扫描 15 秒后仍未发现设备 {mac}，请保持手机蓝牙设置页面开启")
            text = clean_bluetoothctl_output(output.decode("utf-8", errors="replace")).strip()
            if process.poll() is not None:
                raise RuntimeError(text or "bluetoothctl 配对进程意外退出")
            if pair_started:
                raise RuntimeError(text or f"等待设备 {mac} 确认配对超时")
            raise RuntimeError(f"扫描 15 秒后仍未发现设备 {mac}，请保持手机蓝牙设置页面开启")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def paired_in_bluez(mac):
    # 查询实际绑定状态，不以 bluetoothctl 输出的“Pairing successful”代替密钥落盘确认。
    result = subprocess.run(
        ["bluetoothctl", "paired-devices"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=8,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("无法确认 BlueZ 的设备配对状态")
    return any(line.startswith(f"Device {mac} ") for line in clean_bluetoothctl_output(result.stdout).splitlines())


def bluetoothctl_scan(duration=10):
    with bluetoothctl_lock:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = bytearray()
        deadline = time.monotonic() + duration
        try:
            process.stdin.write(b"scan on\n")
            process.stdin.flush()
            while time.monotonic() < deadline:
                readable, _, _ = select.select(
                    [process.stdout],
                    [],
                    [],
                    min(0.5, max(0, deadline - time.monotonic())),
                )
                if readable:
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output.extend(chunk)
                elif process.poll() is not None:
                    break
            text = clean_bluetoothctl_output(output.decode("utf-8", errors="replace"))
            if process.poll() is not None:
                raise RuntimeError(text.strip() or "bluetoothctl 扫描进程意外退出")
            if "Failed to start discovery" in text:
                raise RuntimeError(text.strip())
            process.stdin.write(b"scan off\nquit\n")
            process.stdin.flush()
            process.wait(timeout=3)
            return text
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


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
    output = bluetoothctl("show", timeout=8, check=False)
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
    output = bluetoothctl("info", mac, timeout=8, check=False)
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


def discovered_devices(scan_output=""):
    output = scan_output + "\n" + bluetoothctl("devices", timeout=8, check=False)
    addresses = []
    for line in output.splitlines():
        match = re.search(
            r"(?:^|\]\s)Device\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})(?:\s|$)",
            line.strip(),
        )
        if match:
            mac = match.group(1).upper()
            if mac not in addresses:
                addresses.append(mac)
    return [device_info(mac) for mac in addresses]


def clear_phone_config():
    settings = load_settings()
    for key in ("phone_mac", "hfp_channel", "hfp_channel_detected"):
        settings.pop(key, None)
    save_settings(settings)


def regenerate_saved_config():
    settings = load_settings()
    sip_user = settings.get("sip_user", "1001")
    sip_secret = settings.get("sip_secret", "1234")
    write_sip_config(sip_user, sip_secret)
    return True


def write_sip_config(user, secret):
    user = str(user or "").strip()
    secret = str(secret or "")
    if not SIP_USER_RE.fullmatch(user):
        raise ValueError("SIP 分机只能包含字母、数字、点、下划线和连字符")
    if not secret or len(secret) > 128 or "\n" in secret or "\r" in secret:
        raise ValueError("SIP 密码无效")
    sip_content = asterisk_sip(user, secret)
    extensions_content = asterisk_extensions(user)
    for path, content in (
        (Path("/etc/asterisk/sip.conf"), sip_content),
        (Path("/etc/asterisk/extensions.conf"), extensions_content),
        (STATE_DIR / "baresip" / "config", baresip_config()),
        (STATE_DIR / "baresip" / "accounts", baresip_accounts()),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
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


def configure_phone(mac):
    mac = normalize_mac(mac)
    adapter = bluetooth_show()
    if not adapter["address"]:
        raise RuntimeError("没有检测到可用蓝牙适配器")
    settings = load_settings()
    settings.update({
        "adapter_mac": adapter["address"],
        "phone_mac": mac,
    })
    for key in ("hfp_channel", "hfp_channel_detected"):
        settings.pop(key, None)
    save_settings(settings)
    return mac


def async_operation(kind, message, target):
    if not operations.begin(kind, message):
        raise RuntimeError("另一个蓝牙操作正在进行")

    def worker():
        try:
            result = target()
            print(f"[operation:{kind}] {result or '操作完成'}", flush=True)
            operations.finish(message=str(result or "操作完成"))
        except Exception as exc:
            print(f"[operation:{kind}] 失败: {exc}", flush=True)
            operations.finish(error=str(exc))

    threading.Thread(target=worker, daemon=True, name="gateway-" + kind).start()


def start_scan():
    def action():
        bluetoothctl("power", "on", timeout=8)
        bluetoothctl("pairable", "on", timeout=8)
        try:
            devices = discovered_devices(bluetoothctl_scan(duration=10))
            with operations.lock:
                operations.devices = devices
            return f"扫描完成，发现 {len(devices)} 个设备"
        finally:
            bluetoothctl("pairable", "off", timeout=8, check=False)
            bluetoothctl("discoverable", "off", timeout=8, check=False)

    async_operation("scan", "正在扫描蓝牙设备", action)


def start_pair(mac):
    mac = normalize_mac(mac)

    def action():
        bluetoothctl("power", "on", timeout=8)
        bluetoothctl("pairable", "on", timeout=8)
        bluetoothctl("discoverable", "on", timeout=8, check=False)
        try:
            previous = device_info(mac)
            if previous["paired"] or previous["trusted"]:
                bluetoothctl("remove", mac, timeout=15)
            bluetoothctl_pair(mac, timeout=50)
            if not device_info(mac)["paired"]:
                raise RuntimeError(f"设备 {mac} 未完成配对，请在手机蓝牙设置中删除旧记录后重试")
            bluetoothctl("trust", mac, timeout=10)
            configure_phone(mac)
            bluetoothctl("connect", mac, timeout=20, check=False)
            return "配对完成，正在建立 HFP 控制连接"
        finally:
            bluetoothctl("pairable", "off", timeout=8, check=False)
            bluetoothctl("discoverable", "off", timeout=8, check=False)

    async_operation("pair", "等待手机确认配对", action)


def asterisk_status():
    peers = run(["/usr/sbin/asterisk", "-rx", "sip show peers"], timeout=8, check=False)
    return {
        "ready": "Unable to connect" not in peers and "Name/username" in peers,
        "sip_output": peers[-800:] if peers else "",
    }


def bridge_status():
    path = STATE_DIR / "hfp_bridge.pid"
    try:
        pid = int(path.read_text(encoding="ascii").strip())
        alive = Path(f"/proc/{pid}/comm").read_text(encoding="ascii").strip() == "python3"
    except (OSError, ValueError):
        alive = False
    return {"ready": alive}


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
            "sip_user": settings.get("sip_user", "1001"),
            "sip_has_secret": bool(settings.get("sip_secret")),
        },
        "devices": devices,
        "asterisk": asterisk_status(),
        "hfp": bridge_status(),
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
                configure_phone(mac)
                bluetoothctl("connect", mac, timeout=20)
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/bluetooth/unpair":
                mac = normalize_mac(data.get("mac") or load_settings().get("phone_mac"))
                bluetoothctl("remove", mac, timeout=15)
                clear_phone_config()
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/bluetooth/configure":
                mac = normalize_mac(data.get("mac") or load_settings().get("phone_mac"))
                configure_phone(mac)
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif path == "/api/system/repair":
                async_operation("repair", "正在修复蓝牙环境", lambda: run([CONTROL, "repair"], timeout=60))
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True})
            elif path == "/api/sip/configure":
                configure_sip(data.get("user"), data.get("secret"))
                self.send_json(HTTPStatus.OK, {"ok": True})
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
