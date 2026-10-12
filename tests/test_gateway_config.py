import unittest

from app.gateway_config import asterisk_extensions, asterisk_sip, baresip_accounts, baresip_config


class GatewayConfigTests(unittest.TestCase):
    def test_sip_peer_is_local_only_and_recording_is_bidirectional(self):
        sip = asterisk_sip("1001", "secret")
        extensions = asterisk_extensions("1001")
        self.assertIn("[hfp]\n", sip)
        self.assertIn("host=127.0.0.1\n", sip)
        self.assertIn("context=from-hfp\n", sip)
        self.assertIn("Set(CALLERID(num)=${EXTEN})", extensions)
        self.assertIn("Dial(SIP/hfp@hfp,60,U(record-call^outbound))", extensions)
        self.assertIn("Dial(SIP/1001,30,U(record-call^inbound))", extensions)
        self.assertIn("SIP_HEADER(To)", extensions)
        self.assertIn("Set(CALLERID(num)=${HFP_NUM})", extensions)
        self.assertIn("MixMonitor(/var/spool/asterisk/monitor/", extensions)
        self.assertNotIn("Mobile/", extensions)

    def test_rejects_newlines_in_credentials(self):
        with self.assertRaises(ValueError):
            asterisk_sip("1001", "a\nb")

    def test_baresip_is_loopback_only(self):
        self.assertIn("module_path /usr/lib/baresip/modules", baresip_config())
        self.assertIn("sip_listen 127.0.0.1:5070", baresip_config())
        self.assertIn("ctrl_tcp_listen 127.0.0.1:4444", baresip_config())
        self.assertIn("module menu.so", baresip_config())
        self.assertIn("module account.so", baresip_config())
        self.assertIn("regint=0", baresip_accounts())


if __name__ == "__main__":
    unittest.main()
