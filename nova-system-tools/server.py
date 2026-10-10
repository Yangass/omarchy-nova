#!/usr/bin/env python3
"""Read-only, loopback-only diagnostics API for a local Open WebUI tool."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit


HOST = "127.0.0.1"
PORT = 8787
MAX_PROCESSES = 40
MAX_LOG_LINES = 100
PROC = Path("/proc")
SYS_NET = Path("/sys/class/net")
SENSITIVE_VALUE = re.compile(r"(?i)(authorization|bearer|token|password|secret|api[_-]?key)\s*[:=]\s*\S+")
CPU_LOCK = Lock()
CPU_PREVIOUS: tuple[int, int] | None = None
NETWORK_LOCK = Lock()
NETWORK_PREVIOUS: tuple[float, int, int] | None = None


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path: Path, limit: int = 1_000_000) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as file:
        return file.read(limit)


def parse_kib_file(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in read_text(path).splitlines():
        key, _, value = line.partition(":")
        number = value.strip().split(maxsplit=1)
        if number and number[0].isdigit():
            values[key] = int(number[0])
    return values


def cpu_usage() -> dict[str, Any]:
    global CPU_PREVIOUS
    columns = read_text(PROC / "stat").splitlines()[0].split()
    values = [int(value) for value in columns[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    with CPU_LOCK:
        previous = CPU_PREVIOUS
        CPU_PREVIOUS = (total, idle)
    percent: float | None = None
    if previous:
        total_delta = total - previous[0]
        idle_delta = idle - previous[1]
        if total_delta > 0:
            percent = round(100 * (1 - idle_delta / total_delta), 1)
    load = [round(float(item), 2) for item in read_text(PROC / "loadavg").split()[:3]]
    return {
        "timestamp": now(),
        "usage_percent": percent,
        "note": "The first request establishes a baseline; make a second request for CPU usage.",
        "load_average": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "logical_cores": os.cpu_count(),
    }


def memory_usage() -> dict[str, Any]:
    info = parse_kib_file(PROC / "meminfo")
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    used = max(total - available, 0)
    return {
        "timestamp": now(),
        "memory": {
            "total_mib": round(total / 1024, 1),
            "used_mib": round(used / 1024, 1),
            "available_mib": round(available / 1024, 1),
            "usage_percent": round(100 * used / total, 1) if total else None,
        },
        "swap": {
            "total_mib": round(info.get("SwapTotal", 0) / 1024, 1),
            "free_mib": round(info.get("SwapFree", 0) / 1024, 1),
        },
    }


def gpu_usage() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"timestamp": now(), "available": False, "reason": "nvidia-smi is not installed or not on PATH."}
    command = [
        executable,
        "--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        rows = list(csv.reader(io.StringIO(result.stdout)))
    except (OSError, subprocess.SubprocessError) as error:
        return {"timestamp": now(), "available": False, "reason": f"GPU query failed: {error}"}
    gpus = []
    for row in rows:
        if len(row) == 7:
            gpus.append({
                "name": row[0].strip(), "driver_version": row[1].strip(), "usage_percent": row[2].strip(),
                "memory_used_mib": row[3].strip(), "memory_total_mib": row[4].strip(),
                "temperature_c": row[5].strip(), "power_w": row[6].strip(),
            })
    return {"timestamp": now(), "available": bool(gpus), "gpus": gpus}


def real_mounts() -> list[tuple[str, str]]:
    ignored = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "overlay", "squashfs", "nsfs"}
    mounts: list[tuple[str, str]] = []
    for line in read_text(PROC / "mounts").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[2] not in ignored and fields[1].startswith("/"):
            mounts.append((fields[0], fields[1].replace("\\040", " ")))
    return mounts


def disk_usage() -> dict[str, Any]:
    disks = []
    seen: set[str] = set()
    for source, mountpoint in real_mounts():
        if mountpoint in seen:
            continue
        seen.add(mountpoint)
        try:
            stats = os.statvfs(mountpoint)
            total = stats.f_blocks * stats.f_frsize
            available = stats.f_bavail * stats.f_frsize
        except OSError:
            continue
        used = max(total - available, 0)
        disks.append({"device": source, "mountpoint": mountpoint, "total_gib": round(total / 2**30, 2),
                      "used_gib": round(used / 2**30, 2), "available_gib": round(available / 2**30, 2),
                      "usage_percent": round(100 * used / total, 1) if total else None})
    return {"timestamp": now(), "filesystems": disks}


def physical_interfaces() -> list[str]:
    names = []
    try:
        entries = sorted(SYS_NET.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.name == "lo" or not (SYS_NET / entry.name / "device").exists():
            continue
        names.append(entry.name)
    return names


def network_usage() -> dict[str, Any]:
    global NETWORK_PREVIOUS
    rx_total = 0
    tx_total = 0
    listed: list[dict[str, Any]] = []
    lines = {l.split(":", 1)[0].strip(): l for l in read_text(PROC / "net/dev").splitlines() if ":" in l}
    for name in physical_interfaces():
        line = lines.get(name)
        if line is None:
            continue
        fields = line.split(":", 1)[1].split()
        if len(fields) < 16:
            continue
        rx = int(fields[0])
        tx = int(fields[8])
        rx_total += rx
        tx_total += tx
        listed.append({"name": name, "rx_bytes": rx, "tx_bytes": tx})
    now_mono = time.monotonic()
    rx_rate: int | None = None
    tx_rate: int | None = None
    with NETWORK_LOCK:
        previous = NETWORK_PREVIOUS
        NETWORK_PREVIOUS = (now_mono, rx_total, tx_total)
    if previous and now_mono > previous[0]:
        elapsed = now_mono - previous[0]
        rx_delta = rx_total - previous[1]
        tx_delta = tx_total - previous[2]
        if rx_delta >= 0 and tx_delta >= 0:
            rx_rate = round(rx_delta / elapsed)
            tx_rate = round(tx_delta / elapsed)
    return {
        "timestamp": now(),
        "interfaces": listed,
        "rx_bytes_total": rx_total,
        "tx_bytes_total": tx_total,
        "rx_bytes_per_s": rx_rate,
        "tx_bytes_per_s": tx_rate,
    }


def disk_io() -> dict[str, Any]:
    devices = []
    for line in read_text(PROC / "diskstats").splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        name = fields[2]
        if not (name.startswith(("sd", "nvme", "vd", "xvd", "mmcblk"))):
            continue
        devices.append({"device": name, "reads_completed": int(fields[3]), "read_mib": round(int(fields[5]) * 512 / 2**20, 2),
                        "writes_completed": int(fields[7]), "written_mib": round(int(fields[9]) * 512 / 2**20, 2),
                        "io_time_ms": int(fields[12])})
    return {"timestamp": now(), "cumulative_since_boot": True, "devices": devices}


def temperatures() -> dict[str, Any]:
    readings = []
    for sensor in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            temp = int(read_text(sensor / "temp").strip()) / 1000
            kind = read_text(sensor / "type").strip()
            readings.append({"sensor": kind, "temperature_c": round(temp, 1)})
        except (OSError, ValueError):
            pass
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            chip = read_text(hwmon / "name").strip()
        except OSError:
            chip = hwmon.name
        for path in hwmon.glob("temp*_input"):
            try:
                number = path.name.removesuffix("_input").removeprefix("temp")
                label_path = hwmon / f"temp{number}_label"
                label = read_text(label_path).strip() if label_path.exists() else f"temp{number}"
                readings.append({"sensor": f"{chip}: {label}", "temperature_c": round(int(read_text(path).strip()) / 1000, 1)})
            except (OSError, ValueError):
                pass
    return {"timestamp": now(), "temperatures": readings}


def processes() -> dict[str, Any]:
    items = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = parse_kib_file(entry / "status")
            raw = read_text(entry / "status")
            fields = {line.partition(":")[0]: line.partition(":")[2].strip() for line in raw.splitlines()}
            items.append({"pid": int(entry.name), "name": fields.get("Name", "unknown"), "state": fields.get("State", "?").split(maxsplit=1)[0],
                          "owner_uid": int(fields.get("Uid", "0").split()[0]), "rss_mib": round(status.get("VmRSS", 0) / 1024, 1),
                          "threads": status.get("Threads", 0)})
        except (OSError, ValueError, IndexError):
            continue
    items.sort(key=lambda item: item["rss_mib"], reverse=True)
    return {"timestamp": now(), "sorted_by": "resident_memory", "processes": items[:MAX_PROCESSES], "limit": MAX_PROCESSES}


def system_logs() -> dict[str, Any]:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        return {"timestamp": now(), "available": False, "reason": "journalctl is not available."}
    command = [journalctl, "--boot=0", "--priority=0..4", f"--lines={MAX_LOG_LINES}", "--no-pager", "--output=short-iso"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        lines = [SENSITIVE_VALUE.sub(r"\1=[REDACTED]", line)[:2000] for line in result.stdout.splitlines()]
        return {"timestamp": now(), "available": True, "scope": "current boot; emergency through warning; secrets redacted", "lines": lines}
    except (OSError, subprocess.SubprocessError) as error:
        return {"timestamp": now(), "available": False, "reason": f"Journal query failed: {error}"}


ROUTES = {
    "/v1/cpu": cpu_usage, "/v1/memory": memory_usage, "/v1/gpu": gpu_usage, "/v1/disks": disk_usage, "/v1/network": network_usage,
    "/v1/disk-io": disk_io, "/v1/temperatures": temperatures, "/v1/processes": processes, "/v1/logs": system_logs,
}


def openapi() -> dict[str, Any]:
    paths = {path: {"get": {"operationId": operation, "summary": summary, "responses": {"200": {"description": "Diagnostic data"}}}}
             for path, operation, summary in [
                 ("/v1/cpu", "get_cpu_usage", "Inspect CPU utilization and load."),
                 ("/v1/memory", "get_memory_usage", "Inspect RAM and swap usage."),
                 ("/v1/gpu", "get_gpu_usage", "Inspect NVIDIA GPU utilization, VRAM, temperature, and power."),
                 ("/v1/network", "get_network_usage", "Inspect network download and upload speed of physical interfaces."),
                 ("/v1/disks", "get_disk_usage", "Inspect filesystem capacity."),
                 ("/v1/disk-io", "get_disk_io", "Inspect cumulative disk I/O counters."),
                 ("/v1/temperatures", "get_temperatures", "Inspect CPU and other hardware temperature sensors."),
                 ("/v1/processes", "list_processes", "List up to 40 processes, ordered by RAM use; command arguments are never exposed."),
                 ("/v1/logs", "get_system_logs", "Read up to 100 current-boot warning-or-worse journal lines; sensitive key-value values are redacted."),
             ]}
    paths["/health"] = {"get": {"operationId": "health_check", "summary": "Verify the service is running.", "responses": {"200": {"description": "Service health"}}}}
    return {"openapi": "3.0.3", "info": {"title": "NOVA Read-Only System Tools", "version": "1.0.0", "description": "Loopback-only fixed diagnostics. No shell, no write, no privilege escalation."}, "servers": [{"url": f"http://{HOST}:{PORT}"}], "paths": paths}


# --- owner-only loopback (identical in novad, nova-actions and nova-system-tools) ---
# Every local account can connect to 127.0.0.1, so a loopback port alone does not say
# who is asking (Omarchy marketplace review, 2026-10-10). Before any request is read,
# the kernel's socket table names the account that owns the client end of the
# connection; anything but this desktop user, or an answer that is not exactly one
# account, is refused by closing the connection.
TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")


def _proc_address(text):
    import ipaddress, sys
    host, port = text.split(":")
    raw = bytes.fromhex(host)
    if sys.byteorder == "little": raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    address = ipaddress.ip_address(raw)
    if address.version == 6 and address.ipv4_mapped: address = address.ipv4_mapped
    return str(address), int(port, 16)


def peer_uid(client, server, tables=TCP_TABLES):
    """UID owning the established connection client -> server, or None unless exactly one."""
    owners = set()
    for table in tables:
        try:
            with open(table) as file: rows = file.read().splitlines()[1:]
        except OSError: continue
        for row in rows:
            fields = row.split()
            try:
                if fields[3] != "01": continue  # ESTABLISHED
                if _proc_address(fields[1]) == (client[0], client[1]) and _proc_address(fields[2]) == (server[0], server[1]):
                    owners.add(int(fields[7]))
            except (IndexError, ValueError): continue
    return owners.pop() if len(owners) == 1 else None


class OwnerOnlyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def verify_request(self, request, client_address):
        return peer_uid(client_address, self.server_address[:2]) == os.getuid()
# --- end owner-only loopback ---


class Handler(BaseHTTPRequestHandler):
    server_version = "NovaSystemTools/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return  # Do not write request content into logs.

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:3000")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.query:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Query parameters are not accepted."})
            return
        if parsed.path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "timestamp": now(), "read_only": True})
        elif parsed.path == "/openapi.json":
            self.send_json(HTTPStatus.OK, openapi())
        elif handler := ROUTES.get(parsed.path):
            try:
                self.send_json(HTTPStatus.OK, handler())
            except (OSError, ValueError) as error:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Diagnostic source unavailable.", "detail": str(error)})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:3000")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Id")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self) -> None: self.send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Read-only service; GET only."})
    def do_PUT(self) -> None: self.do_POST()
    def do_PATCH(self) -> None: self.do_POST()
    def do_DELETE(self) -> None: self.do_POST()


if __name__ == "__main__":
    print(f"NOVA system tools listening on http://{HOST}:{PORT}", flush=True)
    OwnerOnlyHTTPServer((HOST, PORT), Handler).serve_forever()
