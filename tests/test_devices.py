from __future__ import annotations

import unittest

from omapath.devices import DeviceMonitor


class DeviceTests(unittest.TestCase):
    def test_volume_monitor_can_be_created_without_a_graphical_display(self) -> None:
        monitor = DeviceMonitor(lambda: None)
        try:
            self.assertIsInstance(monitor.list_devices(), list)
        finally:
            monitor.stop()


if __name__ == "__main__":
    unittest.main()
