#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from app.baresip_control import NetstringBuffer, command_packet
from app.hfp_calls import incoming_sip_uri
from app.hfp_events import CallEvents, modem_ready


STATE_DIR = Path(os.environ.get("GATEWAY_STATE_DIR", "/var/lib/gateway"))
MAC_RE = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z")
OFONO_MANAGER = "org.ofono.Manager"
OFONO_CALL_MANAGER = "org.ofono.VoiceCallManager"
OFONO_CALL = "org.ofono.VoiceCall"


def settings():
    try:
        return json.loads((STATE_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class AudioBridge:
    def __init__(self):
        self.processes = []
        self.mac = ""
        self.log = STATE_DIR / "audio.log"

    def start(self, mac):
        if not MAC_RE.fullmatch(mac):
            raise ValueError("音频桥缺少有效的手机地址")
        self.stop()
        self.mac = mac
        self._spawn()

    def _spawn(self):
        # TODO: 检测协商后的 SCO 编码并兼容 mSBC/16k；目前按现场 CVSD/8k 路径配置。
        device = f"bluealsa:DEV={self.mac},PROFILE=sco"
        streams = (
            (["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "8000", "-F", "20000"],
             ["aplay", "-q", "-D", "plughw:Loopback,1,1", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "8000", "-F", "20000"]),
            (["arecord", "-q", "-D", "plughw:Loopback,1,0", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "8000", "-F", "20000"],
             ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "8000", "-F", "20000"]),
        )
        try:
            with self.log.open("ab", buffering=0) as log:
                for source_args, sink_args in streams:
                    source = subprocess.Popen(source_args, stdout=subprocess.PIPE, stderr=log)
                    self.processes.append(source)
                    sink = subprocess.Popen(sink_args, stdin=source.stdout, stderr=log)
                    source.stdout.close()
                    self.processes.append(sink)
        except Exception:
            self._terminate()
            raise

    def maintain(self):
        if self.mac and (not self.processes or any(process.poll() is not None for process in self.processes)):
            if self.processes:
                print("[hfp] SCO 音频进程退出，尝试重新建立；详见 audio.log", flush=True)
            self._terminate()
            try:
                self._spawn()
            except OSError as exc:
                print("[hfp] SCO 音频进程启动失败:", exc, flush=True)

    def stop(self):
        self.mac = ""
        self._terminate()

    def _terminate(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.processes.clear()


class Gateway:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.audio = AudioBridge()
        self.sock = None
        self.parser = NetstringBuffer()
        self.modem_path = None
        self.modem_signals = []
        self.pending_modems = set()
        self.call_paths = set()
        self.sip_call_id = None
        self.loop = None
        self.error = None
        self.events = CallEvents(self, settings().get("sip_user", "1001"))

    def connect_sip(self):
        for _ in range(30):
            try:
                self.sock = socket.create_connection(("127.0.0.1", 4444), timeout=2)
                self.sock.setblocking(False)
                return
            except OSError:
                time.sleep(1)
        raise RuntimeError("Baresip 控制端口未就绪")

    def send(self, command, params=None):
        # Baresip 的 ctrl_tcp 仅允许一个客户端，保持连接贯穿服务生命周期。
        self.sock.sendall(command_packet(command, params))

    def sip_dial(self, user, caller):
        self.send("dial", incoming_sip_uri(user, caller))

    def sip_accept(self):
        self.send("accept")

    def sip_hangup(self):
        self.send("hangup")

    def gsm_dial(self, number):
        if not self.modem_path:
            raise RuntimeError("手机 HFP 未连接")
        manager = dbus.Interface(self.bus.get_object("org.ofono", self.modem_path), OFONO_CALL_MANAGER)
        return str(manager.Dial(number, "default"))

    def gsm_answer(self, path):
        dbus.Interface(self.bus.get_object("org.ofono", path), OFONO_CALL).Answer()

    def gsm_hangup(self, path):
        dbus.Interface(self.bus.get_object("org.ofono", path), OFONO_CALL).Hangup()

    def gsm_send_tones(self, digit):
        if not self.modem_path:
            raise RuntimeError("手机 HFP 未连接")
        manager = dbus.Interface(self.bus.get_object("org.ofono", self.modem_path), OFONO_CALL_MANAGER)
        manager.SendTones(digit)

    def audio_start(self):
        self.audio.start(settings().get("phone_mac", ""))

    def audio_stop(self):
        self.audio.stop()

    def on_baresip(self, _source, condition):
        try:
            if condition & (GLib.IO_HUP | GLib.IO_ERR):
                raise RuntimeError("Baresip 控制连接已关闭")
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError("Baresip 控制连接已关闭")
            for event in self.parser.feed(data):
                if event.get("response") and not event.get("ok"):
                    # TODO: 为控制命令加 token，关联异步失败响应并主动清理对应通话。
                    print("[sip] 命令失败:", event.get("data"), flush=True)
                elif event.get("class") == "call" and event.get("event"):
                    kind = event.get("type")
                    if kind == "CALL_INCOMING":
                        self.refresh_modems()
                        if not self.modem_path:
                            self.sip_hangup()
                            continue
                    elif kind == "CALL_DTMF_START":
                        if not self.sip_call_id or event.get("id") != self.sip_call_id:
                            continue
                    self.events.sip_event(event)
                    if kind == "CALL_INCOMING" and self.events.calls.direction == "outbound" and not self.sip_call_id:
                        self.sip_call_id = event.get("id")
                    elif kind == "CALL_CLOSED" and event.get("id") == self.sip_call_id:
                        self.sip_call_id = None
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            self.error = exc
            self.loop.quit()
            return False

    def on_modem_added(self, path, props):
        mac = settings().get("phone_mac", "")
        if not MAC_RE.fullmatch(mac):
            return
        if not modem_ready(props, mac):
            if str(path) not in self.pending_modems:
                print("[hfp] 手机调制解调器属性尚未就绪，等待 oFono 补齐", flush=True)
                self.pending_modems.add(str(path))
            return
        if str(path) == self.modem_path:
            return
        if self.modem_path:
            self.on_modem_removed(self.modem_path)
        self.pending_modems.discard(str(path))
        self.modem_path = str(path)
        self.modem_signals = [
            self.bus.add_signal_receiver(self.on_call_added, signal_name="CallAdded", dbus_interface=OFONO_CALL_MANAGER, path=path),
            self.bus.add_signal_receiver(self.on_call_removed, signal_name="CallRemoved", dbus_interface=OFONO_CALL_MANAGER, path=path),
            self.bus.add_signal_receiver(self.on_call_state, signal_name="PropertyChanged", dbus_interface=OFONO_CALL, path_keyword="path"),
        ]
        manager = dbus.Interface(self.bus.get_object("org.ofono", path), OFONO_CALL_MANAGER)
        for call_path, call_props in manager.GetCalls():
            self.on_call_added(call_path, call_props)
        print("[hfp] 已连接手机调制解调器", mac, flush=True)

    def on_call_added(self, path, props):
        path = str(path)
        self.call_paths.add(path)
        self.events.calls.sip_user = settings().get("sip_user", "1001")
        self.events.gsm_added(path, props)

    def on_call_removed(self, path):
        self.call_paths.discard(str(path))
        self.events.gsm_removed(str(path))

    def on_call_state(self, name, value, path=None):
        if path in self.call_paths and str(name) == "State":
            self.events.gsm_state(path, str(value))

    def on_modem_removed(self, path):
        self.pending_modems.discard(str(path))
        if str(path) == self.modem_path:
            for signal in self.modem_signals:
                signal.remove()
            self.modem_signals.clear()
            for call_path in list(self.call_paths):
                self.events.gsm_removed(call_path)
            if self.events.calls.direction:
                self.events.gsm_removed(self.events.calls.gsm_path)
            self.call_paths.clear()
            self.sip_call_id = None
            self.modem_path = None
            self.audio.stop()

    def maintain(self):
        self.audio.maintain()
        self.refresh_modems()
        return True

    def refresh_modems(self):
        manager = dbus.Interface(self.bus.get_object("org.ofono", "/"), OFONO_MANAGER)
        mac = settings().get("phone_mac", "")
        modems = manager.GetModems()
        ready = [(path, props) for path, props in modems if modem_ready(props, mac)]
        if self.modem_path and all(str(path) != self.modem_path for path, _ in ready):
            self.on_modem_removed(self.modem_path)
        if ready:
            self.on_modem_added(*ready[0])

    def run(self):
        self.connect_sip()
        self.loop = GLib.MainLoop()
        GLib.io_add_watch(self.sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_baresip)
        self.bus.add_signal_receiver(self.on_modem_added, signal_name="ModemAdded", dbus_interface=OFONO_MANAGER)
        self.bus.add_signal_receiver(self.on_modem_removed, signal_name="ModemRemoved", dbus_interface=OFONO_MANAGER)
        self.refresh_modems()
        GLib.timeout_add_seconds(2, self.maintain)
        self.loop.run()
        self.audio.stop()
        if self.error:
            raise self.error


if __name__ == "__main__":
    try:
        Gateway().run()
    except Exception as error:
        print("[hfp] 桥接进程退出:", error, file=sys.stderr, flush=True)
        raise
