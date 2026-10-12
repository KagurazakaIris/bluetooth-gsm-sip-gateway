import unittest

from app.hfp_events import CallEvents, modem_ready


class FakePorts:
    def __init__(self):
        self.actions = []

    def sip_dial(self, user):
        self.actions.append(("dial", user))

    def sip_accept(self):
        self.actions.append(("accept",))

    def sip_hangup(self):
        self.actions.append(("sip_hangup",))

    def gsm_dial(self, number):
        self.actions.append(("gsm_dial", number))
        return "/call/2"

    def gsm_answer(self, path):
        self.actions.append(("gsm_answer", path))

    def gsm_hangup(self, path):
        self.actions.append(("gsm_hangup", path))

    def audio_start(self):
        self.actions.append(("audio_start",))

    def audio_stop(self):
        self.actions.append(("audio_stop",))


class EventsTests(unittest.TestCase):
    def setUp(self):
        self.ports = FakePorts()
        self.events = CallEvents(self.ports, "1001")

    def test_sip_outbound_and_modem_answer(self):
        self.events.sip_event({"type": "CALL_INCOMING", "peeruri": "sip:13800138000@127.0.0.1"})
        self.events.gsm_state("/call/2", "active")
        self.assertEqual(self.ports.actions, [("gsm_dial", "13800138000"), ("accept",), ("audio_start",)])
        self.events.gsm_removed("/call/2")
        self.assertEqual(self.ports.actions[-2:], [("audio_stop",), ("sip_hangup",)])

    def test_gsm_inbound_and_sip_answer(self):
        self.events.gsm_added("/call/1", {"State": "incoming", "LineIdentification": "+123"})
        self.events.sip_event({"type": "CALL_ESTABLISHED"})
        self.assertEqual(self.ports.actions, [("dial", "1001"), ("gsm_answer", "/call/1")])
        self.events.gsm_state("/call/1", "active")
        self.assertEqual(self.ports.actions[-1], ("audio_start",))

    def test_bad_sip_number_is_rejected(self):
        self.events.sip_event({"type": "CALL_INCOMING", "peeruri": "sip:hfp@127.0.0.1"})
        self.assertEqual(self.ports.actions, [("sip_hangup",)])

    def test_incoming_waiting_call_is_rejected_without_disrupting_active_call(self):
        self.events.gsm_added("/call/1", {"State": "incoming"})
        self.events.gsm_added("/call/3", {"State": "incoming"})
        self.assertEqual(self.ports.actions, [("dial", "1001"), ("gsm_hangup", "/call/3")])

    def test_modem_is_not_ready_until_ofono_populates_its_properties(self):
        mac = "02:00:00:00:00:01"
        self.assertFalse(modem_ready({"Serial": None, "Interfaces": []}, mac))
        self.assertTrue(modem_ready({"Serial": mac, "Interfaces": ["org.ofono.VoiceCallManager"]}, mac))
        self.assertFalse(modem_ready({"Serial": "02:00:00:00:00:02", "Interfaces": ["org.ofono.VoiceCallManager"]}, mac))

    def test_failed_mobile_dial_rejects_sip_and_clears_state(self):
        def fail(number):
            raise OSError("oFono 拒绝拨号")

        self.ports.gsm_dial = fail
        self.events.sip_event({"type": "CALL_INCOMING", "peeruri": "sip:13800138000@127.0.0.1"})
        self.assertEqual(self.ports.actions, [("sip_hangup",)])
        self.assertIsNone(self.events.calls.direction)

    def test_failed_sip_dial_rejects_mobile_call(self):
        def fail(user):
            raise OSError("Baresip 控制连接异常")

        self.ports.sip_dial = fail
        self.events.gsm_added("/call/1", {"State": "incoming"})
        self.assertEqual(self.ports.actions, [("gsm_hangup", "/call/1")])
        self.assertIsNone(self.events.calls.direction)


if __name__ == "__main__":
    unittest.main()
