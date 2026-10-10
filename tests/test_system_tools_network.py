"""QWEN-14: network_usage() against a temporary /proc- and /sys-class-net-style directory."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "nova-system-tools" / "server.py"
spec = importlib.util.spec_from_file_location("nova_system_tools_server", SERVER)
server = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = server
spec.loader.exec_module(server)

# One /proc line = name: + 8 rx fields + 8 tx fields; rx_bytes is field 0, tx_bytes field 8.
def net_line(name, rx_bytes, tx_bytes, rx_pkts=10, tx_pkts=10) -> str:
    zeros = "0 " * 6
    return f"  {name}: {rx_bytes} {rx_pkts} {zeros}{tx_bytes} {tx_pkts} {zeros}\n"


NET_DEV = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo compressed collisions|errors\n"
    + net_line("lo", 100, 200)
    + net_line("enp5s0", 1_000_000, 250_000)
    + net_line("wlan0", 2_000_000, 500_000)
    + net_line("docker0", 5, 6)
)


class NetworkUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nova network test ")
        root = Path(self.temp.name)
        self.proc = root / "proc"
        self.sysnet = root / "sys" / "class" / "net"
        (self.proc / "net").mkdir(parents=True)
        (self.proc / "net" / "dev").write_text(NET_DEV)
        for name in ("enp5s0", "wlan0"):
            (self.sysnet / name).mkdir(parents=True)
            (self.sysnet / name / "device").write_text("")
        (self.sysnet / "lo").mkdir(parents=True)  # no device -> virtual
        (self.sysnet / "docker0").mkdir(parents=True)  # no device -> virtual
        self.monotonic = 1000.0
        self._patches = [
            mock.patch.object(server, "PROC", self.proc),
            mock.patch.object(server, "SYS_NET", self.sysnet),
            mock.patch.object(server.time, "monotonic", side_effect=lambda: self.monotonic),
        ]
        for p in self._patches:
            p.start()
        server.NETWORK_PREVIOUS = None

    def tearDown(self):
        server.NETWORK_PREVIOUS = None
        for p in reversed(self._patches):
            p.stop()
        self.temp.cleanup()

    def test_only_physical_interfaces_listed_totals_add_up(self):
        result = server.network_usage()
        names = [iface["name"] for iface in result["interfaces"]]
        self.assertEqual(names, ["enp5s0", "wlan0"])
        self.assertEqual(result["rx_bytes_total"], 3_000_000)
        self.assertEqual(result["tx_bytes_total"], 750_000)

    def test_first_call_none_then_rates(self):
        first = server.network_usage()
        self.assertIsNone(first["rx_bytes_per_s"])
        self.assertIsNone(first["tx_bytes_per_s"])
        self.monotonic += 2.0
        # counters advanced: +2 000 000 rx and +500 000 tx over 2 s
        (self.proc / "net" / "dev").write_text(
            NET_DEV.replace(net_line("enp5s0", 1_000_000, 250_000), net_line("enp5s0", 3_000_000, 750_000)))
        second = server.network_usage()
        self.assertEqual(second["rx_bytes_per_s"], 1_000_000)
        self.assertEqual(second["tx_bytes_per_s"], 250_000)

    def test_counter_regression_gives_none_then_recovers(self):
        server.network_usage()
        self.monotonic += 2.0
        (self.proc / "net" / "dev").write_text(NET_DEV.replace("enp5s0: 1000000", "enp5s0: 900000"))
        regressed = server.network_usage()
        self.assertIsNone(regressed["rx_bytes_per_s"])
        self.assertIsNone(regressed["tx_bytes_per_s"])
        self.monotonic += 1.0
        (self.proc / "net" / "dev").write_text(NET_DEV)  # back above the regressed baseline
        recovered = server.network_usage()
        self.assertIsNotNone(recovered["rx_bytes_per_s"])
        self.assertIsNotNone(recovered["tx_bytes_per_s"])

    def test_no_physical_interface(self):
        (self.sysnet / "enp5s0" / "device").unlink()
        (self.sysnet / "wlan0" / "device").unlink()
        result = server.network_usage()
        self.assertEqual(result["interfaces"], [])
        self.assertEqual(result["rx_bytes_total"], 0)
        self.assertEqual(result["tx_bytes_total"], 0)
        self.assertIsNone(result["rx_bytes_per_s"])
        self.assertIsNone(result["tx_bytes_per_s"])

    def test_routes_and_openapi(self):
        self.assertIn("/v1/network", server.ROUTES)
        paths = server.openapi()["paths"]
        self.assertIn("/v1/network", paths)
        self.assertEqual(paths["/v1/network"]["get"]["operationId"], "get_network_usage")


if __name__ == "__main__":
    unittest.main()
