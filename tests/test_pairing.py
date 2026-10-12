import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from app import gateway_manager


class PairingTests(unittest.TestCase):
    def test_bond_status_checks_exact_target(self):
        with patch.object(gateway_manager.subprocess, "run", return_value=CompletedProcess(
            args=["bluetoothctl", "paired-devices"], returncode=0,
            stdout="Device 02:00:00:00:00:02 Other\nDevice 02:00:00:00:00:01 OnePlus\n",
        )):
            self.assertTrue(gateway_manager.paired_in_bluez("02:00:00:00:00:01"))
            self.assertFalse(gateway_manager.paired_in_bluez("02:00:00:00:00:03"))

    def test_pair_output_without_bond_does_not_configure_phone(self):
        mac = "02:00:00:00:00:01"

        def execute(_kind, _message, action):
            action()

        with (
            patch.object(gateway_manager, "async_operation", side_effect=execute),
            patch.object(gateway_manager, "bluetoothctl", return_value=""),
            patch.object(gateway_manager, "bluetoothctl_pair", return_value="Pairing successful"),
            patch.object(gateway_manager, "device_info", return_value={"paired": False, "trusted": False}),
            patch.object(gateway_manager, "configure_phone") as configure,
            patch.object(gateway_manager.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "未完成配对"):
                gateway_manager.start_pair(mac)
            configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
