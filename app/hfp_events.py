from .hfp_calls import HfpCallRouter, sip_caller_number


def modem_ready(properties, mac):
    return (
        bool(mac)
        and str(properties.get("Serial", "")).upper() == mac
        and "org.ofono.VoiceCallManager" in properties.get("Interfaces", [])
    )


class CallEvents:
    def __init__(self, ports, sip_user):
        self.ports = ports
        self.calls = HfpCallRouter(ports, sip_user)
        self.audio_running = False

    def sip_event(self, event):
        kind = event.get("type")
        if kind == "CALL_INCOMING":
            try:
                self.calls.sip_incoming(sip_caller_number(event.get("peeruri", "")))
            except Exception as exc:
                print("[hfp] 无法接通 SIP 去电:", exc, flush=True)
                self.ports.sip_hangup()
        elif kind == "CALL_ESTABLISHED":
            try:
                self.calls.sip_established()
            except Exception as exc:
                print("[hfp] 手机接听失败:", exc, flush=True)
                try:
                    self.calls.sip_closed()
                finally:
                    self.ports.sip_hangup()
        elif kind == "CALL_CLOSED":
            self._stop_audio()
            self.calls.sip_closed()

    def gsm_added(self, path, properties):
        if str(properties.get("State")) in ("incoming", "waiting"):
            try:
                self.calls.gsm_incoming(path, str(properties.get("LineIdentification", "")))
            except Exception as exc:
                print("[hfp] 无法转接手机来电:", exc, flush=True)
                self.ports.gsm_hangup(path)
        elif str(properties.get("State")) == "active":
            self.gsm_state(path, "active")

    def gsm_state(self, path, state):
        if state == "active" and path == self.calls.gsm_path:
            try:
                self.calls.gsm_active(path)
                if not self.audio_running:
                    self.ports.audio_start()
                    self.audio_running = True
            except Exception as exc:
                print("[hfp] 通话建立或音频启动失败:", exc, flush=True)
                self._stop_audio()
                try:
                    self.calls.sip_closed()
                finally:
                    self.ports.sip_hangup()
        elif state == "disconnected" and path == self.calls.gsm_path:
            self.gsm_removed(path)

    def gsm_removed(self, path):
        if path == self.calls.gsm_path:
            self._stop_audio()
            self.calls.gsm_removed(path)

    def _stop_audio(self):
        if self.audio_running:
            self.ports.audio_stop()
            self.audio_running = False
