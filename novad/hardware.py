"""Read-only local facts. Capacity drives fit; samples never reduce that capacity."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import time
import urllib.request

MIB = 1024 ** 2


def number(value):
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n >= 0 and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def read(path):
    try: return Path(path).read_text(errors='replace')[:1_000_000].strip()
    except OSError: return ''


def command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
        return r.stdout[:1_000_000] if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError): return ''


API_KEY_FILE = Path.home() / '.config' / 'nova' / 'api.token'


def _nova_key_headers():
    """nova-system-tools needs the per-user key (0.14.1). Without it the GPU telemetry
    quietly came back empty and the RTX's memory size was lost (found 2026-10-12)."""
    try: key = API_KEY_FILE.read_text().strip()
    except OSError: return {}
    return {'X-Nova-Key': key} if len(key) >= 32 else {}


def telemetry(path):
    try:
        request = urllib.request.Request('http://127.0.0.1:8787/v1/' + path, headers=_nova_key_headers())
        with urllib.request.urlopen(request, timeout=4) as r:
            value = json.loads(r.read(1_000_000))
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def storage_profile():
    # Inspect only service configuration, never arbitrary process environments.
    raw = command(['systemctl', 'show', 'ollama.service', '-p', 'Environment', '-p', 'User'])
    props = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    try: env = dict(item.split('=', 1) for item in shlex.split(props.get('Environment', '')) if '=' in item)
    except ValueError: env = {}
    location = env.get('OLLAMA_MODELS')
    source = 'ollama.service OLLAMA_MODELS'
    if not location:
        home = env.get('HOME')
        if not home and props.get('User'):
            try: home = pwd.getpwnam(props['User']).pw_dir
            except KeyError: pass
        if home:
            location = str(Path(home) / '.ollama/models')
            source = 'ollama.service home default (unverified override)'
    if not location or not Path(location).is_absolute():
        return {'total_bytes': None, 'free_bytes': None, 'source': 'unknown model-storage filesystem'}
    path = Path(location)
    # An uncreated models directory still lives on its nearest existing parent.
    while not path.exists() and path != path.parent: path = path.parent
    try:
        usage = shutil.disk_usage(path)
        return {'total_bytes': usage.total, 'free_bytes': usage.free, 'source': source,
                'note': 'Free space is a dynamic sample; model paths are not exposed.'}
    except OSError: return {'total_bytes': None, 'free_bytes': None, 'source': source}


def collect_profile(provider_available=None):
    blocks = read('/proc/cpuinfo').split('\n\n')
    cpu_rows = [dict((k.strip(), v.strip()) for k, v in
                    (line.split(':', 1) for line in block.splitlines() if ':' in line)) for block in blocks]
    pairs = {(r['physical id'], r['core id']) for r in cpu_rows if 'physical id' in r and 'core id' in r}
    mem = {}
    for line in read('/proc/meminfo').splitlines():
        parts = line.split()
        if len(parts) >= 2: mem[parts[0].rstrip(':')] = number(parts[1])
    def memory_bytes(key):
        value = mem.get(key)
        return value * 1024 if value is not None else None
    pci_names = {}
    for line in command(['lspci', '-Dmm']).splitlines():
        try:
            fields = shlex.split(line)
            if len(fields) >= 4: pci_names[fields[0]] = fields[3]
        except ValueError: pass
    devices, samples = [], []
    for path in sorted(Path('/sys/bus/pci/devices').glob('*')):
        if not read(path / 'class').startswith('0x03'): continue
        vendor = {'0x10de': 'nvidia', '0x1002': 'amd', '0x8086': 'intel'}.get(read(path / 'vendor'), 'unknown')
        total = number(read(path / 'mem_info_vram_total'))
        used = number(read(path / 'mem_info_vram_used'))
        devices.append({'id': 'gpu' + str(len(devices)), 'vendor': vendor, 'model': pci_names.get(path.name),
                        'vram_total_bytes': total, 'memory_kind': 'unknown', 'source': 'PCI/sysfs',
                        'driver': (path / 'driver').resolve().name if (path / 'driver').exists() else None})
        samples.append({'id': devices[-1]['id'], 'vram_used_bytes': used,
                        'vram_available_bytes': max(0, total-used) if total is not None and used is not None else None})
    gpu = telemetry('gpu')
    rows = gpu.get('gpus') if isinstance(gpu.get('gpus'), list) else []
    nvidia = [d for d in devices if d['vendor'] == 'nvidia']
    # No UUID/PCI identifiers exposed. Only associate telemetry when unambiguous.
    if len(rows) == 1 and isinstance(rows[0], dict) and len(nvidia) <= 1:
        row = rows[0]
        if not nvidia:
            devices.append({'id': 'gpu' + str(len(devices)), 'vendor': 'nvidia'})
            samples.append({'id': devices[-1]['id']})
            nvidia = [devices[-1]]
        device = nvidia[0]
        total, used = number(row.get('memory_total_mib')), number(row.get('memory_used_mib'))
        device.update(model=row.get('name') if isinstance(row.get('name'), str) else None,
                      vram_total_bytes=total*MIB if total is not None else None,
                      memory_kind='dedicated', source='nova-system-tools/nvidia-smi',
                      driver=row.get('driver_version'))
        sample = next(s for s in samples if s['id'] == device['id'])
        sample.update(vram_used_bytes=used*MIB if used is not None else None,
                      vram_available_bytes=max(0, total-used)*MIB if total is not None and used is not None else None,
                      utilization_percent=number(row.get('usage_percent')))
    elif len(rows) > 1:
        # Aggregate inventory is accurate; do not invent telemetry-to-PCI mapping.
        devices = [d for d in devices if d['vendor'] != 'nvidia']
        samples = [s for s in samples if any(d['id'] == s['id'] for d in devices)]
        for index, row in enumerate(rows):
            if not isinstance(row, dict): continue
            total, used = number(row.get('memory_total_mib')), number(row.get('memory_used_mib'))
            identifier = 'nvidia' + str(index)
            devices.append({'id': identifier, 'vendor': 'nvidia', 'model': row.get('name'), 'memory_kind': 'dedicated',
                            'vram_total_bytes': total*MIB if total is not None else None, 'source': 'nova-system-tools/nvidia-smi'})
            samples.append({'id': identifier, 'vram_used_bytes': used*MIB if used is not None else None,
                            'vram_available_bytes': max(0,total-used)*MIB if total is not None and used is not None else None})
    cuda_match = re.search(r'CUDA Version:\s*([0-9.]+)', command(['nvidia-smi'])) if nvidia else None
    return {'schema_version': 1, 'sampled_at': time.time(),
            'capacity': {'cpu': {'model': next((r.get('model name') or r.get('Hardware') for r in cpu_rows if r.get('model name') or r.get('Hardware')), None),
                                 'logical_cores': os.cpu_count(), 'physical_cores': len(pairs) or None},
                         'memory': {'total_bytes': memory_bytes('MemTotal')}, 'gpus': devices},
            'dynamic': {'memory': {'available_bytes': memory_bytes('MemAvailable')}, 'gpus': samples,
                        'storage': storage_profile()},
            'local_ai': {'provider': 'ollama', 'available': provider_available, 'acceleration': 'unknown',
                         'inference_device': None, 'cuda_driver_api_version': cuda_match.group(1) if cuda_match else None,
                         'cuda_source': 'nvidia-smi driver-supported API; not proof of Ollama CUDA use', 'note': 'GPU discovery does not prove Ollama backend/device selection.'},
            'sources': ['/proc/cpuinfo', '/proc/meminfo', 'PCI/sysfs', 'nova-system-tools GPU telemetry', 'ollama.service configuration + filesystem stat'],
            'sampling_caveat': 'Dynamic samples include NOVA/Ollama and other applications. Fit uses capacity, not current utilization.'}
