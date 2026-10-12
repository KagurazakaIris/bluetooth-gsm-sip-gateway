import json
import unittest

from app.baresip_control import NetstringBuffer, command_packet


class BaresipProtocolTests(unittest.TestCase):
    def test_split_and_combined_frames(self):
        parser = NetstringBuffer()
        first = json.dumps({"type": "CALL_INCOMING"}).encode()
        second = json.dumps({"type": "CALL_CLOSED"}).encode()
        wire = str(len(first)).encode() + b":" + first + b"," + str(len(second)).encode() + b":" + second + b","
        self.assertEqual(parser.feed(wire[:8]), [])
        self.assertEqual(parser.feed(wire[8:]), [json.loads(first), json.loads(second)])

    def test_rejects_invalid_and_oversized_frames(self):
        for wire in (b"hello", b"1000000:{}", b"2:{}!", b"2:xx,"):
            with self.subTest(wire=wire), self.assertRaises(ValueError):
                NetstringBuffer().feed(wire)

    def test_command_packet_encodes_json_netstring(self):
        packet = command_packet("dial", "sip:1001@127.0.0.1:5060")
        self.assertEqual(NetstringBuffer().feed(packet), [{"command": "dial", "params": "sip:1001@127.0.0.1:5060"}])


if __name__ == "__main__":
    unittest.main()
