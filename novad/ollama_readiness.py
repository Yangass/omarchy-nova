"""Read-only proof for the audited systemd Ollama deployment preset.

Unsupported unit layouts/overrides return unknown, never a guessed write grant.
"""
import hashlib
import os
from pathlib import Path
import pwd
import stat

from catalog_observability import OLLAMA_STORAGE

UNIT = Path('/usr/lib/systemd/system/ollama.service')
UNIT_SHA256 = '9177dd27de7ec74cf4f74790e0d1db373f0da3fd6efe3e856b089e0124a4c1ed'


def service_process_starts(pids, owner, proc=Path('/proc')):
    """Start ticks of the ollama server and any runners, or None if any process is foreign."""
    if not 1 <= len(pids) <= 16 or not all(pid.isdigit() for pid in pids): return None
    starts = []
    for pid in pids:
        process = proc / pid
        fields = dict(line.split(':', 1) for line in (process/'status').read_text().splitlines() if ':' in line)
        if fields.get('Name', '').strip() != 'ollama': return None
        if set(map(int, fields['Uid'].split())) != {owner}: return None
        if (process/'cgroup').read_text().strip() != '0::/system.slice/ollama.service': return None
        starts.append(int((process/'stat').read_text().rsplit(')', 1)[1].split()[19]))
    return starts


def write_readiness():
    try:
        if OLLAMA_STORAGE != Path('/var/lib/ollama'): return None
        for root in ('/etc/systemd/system', '/run/systemd/system', '/usr/lib/systemd/system'):
            base = Path(root)
            if root != '/usr/lib/systemd/system' and (base/'ollama.service').exists(): return None
            if any((base/'ollama.service.d').glob('*.conf')) or any((base/'service.d').glob('*.conf')): return None
        info = UNIT.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022: return None
        if hashlib.sha256(UNIT.read_bytes()).hexdigest() != UNIT_SHA256: return None
        # This preset grants /var writes and does not remap /var or service users.
        owner = pwd.getpwnam('ollama').pw_uid
        pids = Path('/sys/fs/cgroup/system.slice/ollama.service/cgroup.procs').read_text().split()
        # The server plus any model runners it spawned: every process must be the
        # ollama binary, run by the ollama user, inside exactly this service.
        starts = service_process_starts(pids, owner)
        if starts is None: return None
        # Reject a unit edited after service start (the earliest process): it might not be effective yet.
        boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
        ticks = min(starts)
        if info.st_mtime > boot + ticks / os.sysconf('SC_CLK_TCK'): return None
        if Path('/sys/fs/selinux/enforce').exists(): return None
        apparmor = Path('/sys/module/apparmor/parameters/enabled')
        if apparmor.exists() and apparmor.read_text().strip() != 'N': return None
        for path in (OLLAMA_STORAGE, OLLAMA_STORAGE/'blobs', OLLAMA_STORAGE/'manifests'):
            s = path.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != owner: return None
            if s.st_mode & 0o300 != 0o300: return False
            if os.statvfs(path).f_flag & os.ST_RDONLY: return False
        count = 0
        for directory, children, _files in os.walk(OLLAMA_STORAGE/'manifests'):
            count += 1
            if count > 512: return None
            for child in children:
                s = (Path(directory)/child).lstat()
                if not stat.S_ISDIR(s.st_mode) or s.st_uid != owner: return None
                if s.st_mode & 0o300 != 0o300: return False
        for path in (Path('/'), Path('/var'), Path('/var/lib')):
            if path.is_symlink() or path.stat().st_mode & 0o001 == 0: return None
        return True
    except (OSError, ValueError, KeyError, StopIteration): return None
