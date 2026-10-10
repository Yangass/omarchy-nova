"""QWEN-14: novad proxies /telemetry/network to the system tools endpoint."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("nova_server_under_test", HERE / "server.py")
server = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = server
spec.loader.exec_module(server)


class TelemetryNetworkRouteTests(unittest.TestCase):
    def test_route_map_entry(self):
        self.assertEqual(server.TELEMETRY_ROUTES["/telemetry/network"], "/v1/network")


if __name__ == "__main__":
    unittest.main()
