import re


NUMBER_RE = re.compile(r"\+?[0-9*#]{1,80}\Z")
SIP_NUMBER_RE = re.compile(r"sip:(\+?[0-9*#]{1,80})@[A-Za-z0-9.:-]+(?:;[A-Za-z0-9=;_-]+)?\Z")
CALLER_NUMBER_RE = re.compile(r"\+?[0-9*#]{1,80}\Z")
CALLER_SEPARATORS = str.maketrans("", "", " -().")


def sip_caller_number(peer_uri):
    match = SIP_NUMBER_RE.fullmatch(peer_uri)
    if not match:
        raise ValueError("SIP 主叫号码格式无效")
    return match.group(1)


def incoming_caller_number(caller):
    raw = str(caller or "")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return None
    number = raw.translate(CALLER_SEPARATORS)
    return number if CALLER_NUMBER_RE.fullmatch(number) else None


def incoming_sip_uri(user, caller):
    uri = f"sip:{user}@127.0.0.1:5060"
    number = incoming_caller_number(caller)
    return f"{uri};caller={number}" if number else uri


class HfpCallRouter:
    def __init__(self, ports, sip_user):
        self.ports = ports
        self.sip_user = sip_user
        self.direction = None
        self.gsm_path = None
        self.answered = False

    def gsm_incoming(self, path, caller):
        if self.direction:
            # TODO: 后续支持呼叫等待；目前只接受一通电话。
            raise RuntimeError("已有通话，无法接受新的手机来电")
        self.direction = "inbound"
        self.gsm_path = path
        try:
            self.ports.sip_dial(self.sip_user, incoming_caller_number(caller))
        except Exception:
            self._clear()
            raise

    def sip_incoming(self, number):
        if not NUMBER_RE.fullmatch(number):
            raise ValueError("拨号号码格式无效")
        if self.direction:
            raise RuntimeError("已有通话，无法接受新的 SIP 去电")
        self.direction = "outbound"
        try:
            self.gsm_path = self.ports.gsm_dial(number)
        except Exception:
            self._clear()
            raise

    def sip_established(self):
        if self.direction == "inbound" and self.gsm_path and not self.answered:
            self.ports.gsm_answer(self.gsm_path)
            self.answered = True

    def gsm_active(self, path):
        if self.direction == "outbound" and path == self.gsm_path and not self.answered:
            self.ports.sip_accept()
            self.answered = True

    def sip_closed(self):
        if self.direction and self.gsm_path:
            path = self.gsm_path
            self._clear()
            self.ports.gsm_hangup(path)

    def gsm_removed(self, path):
        if self.direction and self.gsm_path == path:
            self._clear()
            self.ports.sip_hangup()

    def _clear(self):
        self.direction = None
        self.gsm_path = None
        self.answered = False
