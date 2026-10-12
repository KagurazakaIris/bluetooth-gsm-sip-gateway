import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(os.environ.get("GATEWAY_SCRIPT_DIR", Path(__file__).resolve().parents[1] / "scripts")) / "reconnect-phone.sh"


class ReconnectTests(unittest.TestCase):
    def run_reconnect(self, paired):
        with tempfile.TemporaryDirectory() as folder:
            bin_dir = Path(folder)
            (bin_dir / "bluetoothctl").write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = paired-devices ]; then\n"
                f"  printf '%s\\n' '{paired}'\n"
                "else\n"
                f"  cat >'{bin_dir}/actions'\n"
                "fi\n"
            )
            (bin_dir / "bluetoothctl").chmod(0o755)
            (bin_dir / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
            (bin_dir / "timeout").chmod(0o755)
            environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
            result = subprocess.run(
                ["bash", str(SCRIPT), "02:00:00:00:00:01"],
                env=environment, capture_output=True, text=True, timeout=5,
            )
            actions = (bin_dir / "actions").read_text() if (bin_dir / "actions").exists() else ""
            return result.returncode, actions

    def test_unpaired_phone_does_not_get_trusted_or_connected(self):
        status, actions = self.run_reconnect("Device 02:00:00:00:00:02 Other")
        self.assertEqual(status, 0)
        self.assertEqual(actions, "")

    def test_paired_phone_is_reconnected(self):
        status, actions = self.run_reconnect("Device 02:00:00:00:00:01 Phone")
        self.assertEqual(status, 0)
        self.assertIn("connect 02:00:00:00:00:01\n", actions)


if __name__ == "__main__":
    unittest.main()
