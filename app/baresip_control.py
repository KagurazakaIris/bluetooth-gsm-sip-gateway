import json


class NetstringBuffer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        messages = []
        while self.buffer:
            sep = self.buffer.find(b":")
            if sep == -1:
                if len(self.buffer) > 8 or not self.buffer.isdigit():
                    raise ValueError("无效的 Baresip 帧长度")
                break
            header = self.buffer[:sep]
            if not header.isdigit() or len(header) > 6:
                raise ValueError("无效的 Baresip 帧长度")
            size = int(header)
            if size > 65536:
                raise ValueError("Baresip 帧过大")
            end = sep + 1 + size
            if len(self.buffer) < end + 1:
                break
            if self.buffer[end] != ord(","):
                raise ValueError("Baresip 帧终止符无效")
            messages.append(json.loads(self.buffer[sep + 1:end]))
            del self.buffer[:end + 1]
        return messages


def command_packet(command, params=None):
    payload = {"command": command}
    if params is not None:
        payload["params"] = params
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data + b","
