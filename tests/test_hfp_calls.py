import unittest

from app.hfp_calls import HfpCallRouter, sip_caller_number


class FakePorts:
    def __init__(self):
        self.actions = []

    def sip_dial(self, number):
        self.actions.append(("sip_dial", number))

    def sip_accept(self):
        self.actions.append(("sip_accept",))

    def sip_hangup(self):
        self.actions.append(("sip_hangup",))

    def gsm_dial(self, number):
        self.actions.append(("gsm_dial", number))
        return "/call/2"

    def gsm_answer(self, path):
        self.actions.append(("gsm_answer", path))

    def gsm_hangup(self, path):
        self.actions.append(("gsm_hangup", path))


class CallRouterTests(unittest.TestCase):
    def setUp(self):
        self.ports = FakePorts()
        self.router = HfpCallRouter(self.ports, "1001")

    def test_gsm_incoming_waits_for_sip_answer(self):
        self.router.gsm_incoming("/call/1", "12345")
        self.assertEqual(self.ports.actions, [("sip_dial", "1001")])
        self.router.sip_established()
        self.assertEqual(self.ports.actions[-1], ("gsm_answer", "/call/1"))

    def test_sip_outbound_waits_for_gsm_answer(self):
        self.router.sip_incoming("13800138000")
        self.assertEqual(self.ports.actions, [("gsm_dial", "13800138000")])
        self.router.gsm_active("/call/2")
        self.assertEqual(self.ports.actions[-1], ("sip_accept",))

    def test_rejects_concurrent_call_and_invalid_number(self):
        self.router.gsm_incoming("/call/1", "12345")
        with self.assertRaises(ValueError):
            self.router.sip_incoming("1001@evil")
        with self.assertRaises(RuntimeError):
            self.router.sip_incoming("13800138000")
        self.assertEqual(self.ports.actions, [("sip_dial", "1001")])

    def test_sip_hangup_releases_gsm_and_clears_state(self):
        self.router.gsm_incoming("/call/1", "12345")
        self.router.sip_closed()
        self.assertEqual(self.ports.actions[-1], ("gsm_hangup", "/call/1"))
        self.router.sip_incoming("13800138000")
        self.assertEqual(self.ports.actions[-1], ("gsm_dial", "13800138000"))

    def test_gsm_hangup_releases_sip_and_clears_state(self):
        self.router.sip_incoming("13800138000")
        self.router.gsm_active("/call/2")
        self.router.gsm_removed("/call/2")
        self.assertEqual(self.ports.actions[-1], ("sip_hangup",))
        self.router.gsm_incoming("/call/3", "12345")
        self.assertEqual(self.ports.actions[-1], ("sip_dial", "1001"))

    def test_sip_caller_number_rejects_non_numeric_or_malformed_uri(self):
        self.assertEqual(sip_caller_number("sip:+12345@127.0.0.1"), "+12345")
        with self.assertRaises(ValueError):
            sip_caller_number("sip:hfp@127.0.0.1")
        with self.assertRaises(ValueError):
            sip_caller_number("sip:12345@127.0.0.1;transport=tcp\nFoo: bar")

    def test_duplicate_events_do_not_answer_or_hangup_twice(self):
        self.router.sip_incoming("13800138000")
        self.router.gsm_active("/call/2")
        self.router.gsm_active("/call/2")
        self.assertEqual(self.ports.actions.count(("sip_accept",)), 1)
        self.router.gsm_removed("/call/2")
        self.router.sip_closed()
        self.assertEqual(self.ports.actions.count(("sip_hangup",)), 1)

    def test_rejects_second_gsm_call_without_disrupting_first(self):
        self.router.gsm_incoming("/call/1", "12345")
        with self.assertRaises(RuntimeError):
            self.router.gsm_incoming("/call/3", "67890")
        self.router.sip_established()
        self.assertEqual(self.ports.actions[-1], ("gsm_answer", "/call/1"))

    def test_failed_answer_can_be_retried(self):
        self.router.gsm_incoming("/call/1", "+123")
        original = self.ports.gsm_answer

        def fail_once(path):
            self.ports.gsm_answer = original
            raise RuntimeError("手机暂时无法接听")

        self.ports.gsm_answer = fail_once
        with self.assertRaises(RuntimeError):
            self.router.sip_established()
        self.router.sip_established()
        self.assertEqual(self.ports.actions[-1], ("gsm_answer", "/call/1"))


if __name__ == "__main__":
    unittest.main()
