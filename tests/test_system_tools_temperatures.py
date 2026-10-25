"""The processor's temperature and limit, and the graphics card's limit (MACHINE temperature
bars, Konstantin 2026-10-25): against temporary /sys/class/hwmon-style trees and a fake
nvidia-smi."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "nova-system-tools" / "server.py"
sys.path.insert(0, str(SERVER.parent))  # its local_socket module
spec = importlib.util.spec_from_file_location("nova_system_tools_server_temps", SERVER)
server = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = server
spec.loader.exec_module(server)


def chip(root: Path, index: int, name: str, sensors: dict[str, dict[str, int]]) -> None:
    """hwmon<index> called `name`, with sensors {label: {"input": m°C, "crit": m°C, ...}}."""
    hwmon = root / f"hwmon{index}"
    hwmon.mkdir()
    (hwmon / "name").write_text(name + "\n")
    for k, (label, values) in enumerate(sensors.items(), start=1):
        (hwmon / f"temp{k}_label").write_text(label + "\n")
        for kind, value in values.items():
            (hwmon / f"temp{k}_{kind}").write_text(f"{value}\n")


class CpuTemperatureTests(unittest.TestCase):
    def test_an_amd_ryzen_uses_tctl_and_amds_95_degrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chip(root, 0, "nvme", {"Composite": {"input": 61900}})
            chip(root, 1, "k10temp", {"Tccd1": {"input": 40600}, "Tctl": {"input": 47600}})
            cpu = server.cpu_temperature(root)
        self.assertEqual(cpu, {"sensor": "k10temp: Tctl", "temperature_c": 47.6,
                               "limit_c": 95, "limit_source": "AMD Ryzen limit"})

    def test_an_intel_package_uses_the_sensors_own_critical_temperature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chip(root, 0, "coretemp", {"Core 0": {"input": 50000},
                                       "Package id 0": {"input": 55000, "crit": 105000, "max": 85000}})
            cpu = server.cpu_temperature(root)
        self.assertEqual(cpu["sensor"], "coretemp: Package id 0")
        self.assertEqual((cpu["temperature_c"], cpu["limit_c"]), (55.0, 105.0))
        self.assertEqual(cpu["limit_source"], "the sensor's crit")

    def test_a_silly_limit_is_ignored_and_no_cpu_sensor_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chip(root, 0, "k10temp", {"Tctl": {"input": 47000, "crit": 255000}})
            self.assertEqual(server.cpu_temperature(root)["limit_c"], 95)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chip(root, 0, "nvme", {"Composite": {"input": 30000}})
            self.assertIsNone(server.cpu_temperature(root))


class GpuLimitTests(unittest.TestCase):
    def test_the_limit_is_now_plus_what_is_left(self) -> None:
        self.assertEqual(server.gpu_temperature_limit("47", "43"), "90")
        self.assertEqual(server.gpu_temperature_limit("47", "[N/A]"), "")
        self.assertEqual(server.gpu_temperature_limit("", "43"), "")
        self.assertEqual(server.gpu_temperature_limit("47", "400"), "", "out of range")

    def test_an_older_driver_is_asked_again_without_the_limit(self) -> None:
        calls: list[list[str]] = []

        def run(command, **_):
            calls.append(command)
            if "tlimit" in command[1]:
                raise subprocess.CalledProcessError(2, command)
            return subprocess.CompletedProcess(command, 0, stdout="RTX 2060, 550.1, 12, 1000, 6144, 61, 80.5\n")

        with mock.patch.object(server.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                mock.patch.object(server.subprocess, "run", side_effect=run):
            data = server.gpu_usage()
        self.assertEqual(len(calls), 2)
        self.assertTrue(data["available"])
        self.assertEqual(data["gpus"][0]["temperature_c"], "61")
        self.assertEqual(data["gpus"][0]["temperature_limit_c"], "")

    def test_a_current_driver_reports_the_limit(self) -> None:
        def run(command, **_):
            return subprocess.CompletedProcess(command, 0, stdout="RTX 5090, 615.7, 3, 1387, 32607, 47, 16.4, 43\n")

        with mock.patch.object(server.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                mock.patch.object(server.subprocess, "run", side_effect=run):
            data = server.gpu_usage()
        self.assertEqual(data["gpus"][0]["temperature_limit_c"], "90")


if __name__ == "__main__":
    unittest.main()
