"""Follow Ollama when it re-publishes a shipped catalog tag, within a strict guard.

Each shipped catalog model pins one exact registry manifest. Ollama re-publishes
tags (new template, new weights), and the download is verified against the pin
only after it finishes, so a drifted pin used to mean a full download and then
FAILED. Before an install is proposed, ``refresh`` reads the live manifest (a few
KB). Same digest: nothing changes. Changed: the model is probed again without
downloading it, and when it is still the same model (same architecture family and
quantization, size within ``MAX_SIZE_CHANGE_PCT``) the new pin is stored in a
private overlay under ``$XDG_STATE_HOME/nova/repinned``. The proposal, the
approval and the post-download verification then all use that one new pin.

An overlay row only applies while the shipped record still pins the manifest it
replaced (``repin_of``): a NOVA update that ships a newer list supersedes it.
Curated fields (name, family, task tags and their citations) always come from the
shipped list; only identity, size and declared metadata come from the probe.

Every registry read goes through an injectable ``fetch`` (tests pass a fake).
"""
from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import catalog_drift
import registry_probe

MAX_SIZE_CHANGE_PCT = 25
MANIFEST_TIMEOUT_SECONDS = 10
PROBE_TIMEOUT_SECONDS = 15  # per read; the propose call in the panel waits 45 s
CURATED = ('catalog_id', 'display_name', 'family', 'release_label', 'task_tags')


def default_store() -> Path:
    base = os.environ.get('XDG_STATE_HOME')
    root = Path(base).expanduser() if base else Path.home() / '.local' / 'state'
    return root / 'nova' / 'repinned'


def _fetch_with_timeout(seconds: int):
    def fetch(url: str, headers: dict[str, str] | None = None, limit: int = 1 << 20) -> bytes:
        request = urllib.request.Request(url, headers={'User-Agent': 'nova-catalog-probe', **(headers or {})})
        with urllib.request.urlopen(request, timeout=seconds) as response:
            return response.read(limit)
    return fetch


# registry_probe.http_get waits 60 s per read; an install click must not hang that long.
manifest_fetch = _fetch_with_timeout(MANIFEST_TIMEOUT_SECONDS)
probe_fetch = _fetch_with_timeout(PROBE_TIMEOUT_SECONDS)


def live_digest(upstream_id: str, fetch=manifest_fetch) -> tuple[str, str | None]:
    """(status, manifest sha256): ok | gone | unreachable | invalid (QWEN-18's manifest_check)."""
    check = catalog_drift.manifest_check(upstream_id, fetch=fetch)
    return check['status'], check['manifest_sha256'] if check['status'] == 'ok' else None


def _family(architecture) -> str | None:
    # gguf spellings drift ('gptoss' became 'gpt-oss' in 2026-10); compare letters and digits only.
    return re.sub(r'[^a-z0-9]', '', architecture.lower()) if isinstance(architecture, str) else None


def guard(shipped: dict, probed: dict) -> list[str]:
    """Reasons the re-published model is not the model NOVA's list describes; empty when it is."""
    reasons = []
    old, new = shipped.get('properties', {}), probed.get('properties', {})
    if _family(old.get('architecture')) != _family(new.get('architecture')) or _family(new.get('architecture')) is None:
        reasons.append('architecture_changed')
    if (old.get('quantizations') or [None])[0] != (new.get('quantizations') or [None])[0]:
        reasons.append('quantization_changed')
    before, after = shipped.get('acquisition', {}).get('expected_size_bytes'), new.get('weight_size_bytes')
    if type(before) is not int or type(after) is not int or before <= 0:
        reasons.append('size_unknown')
    elif abs(after - before) * 100 > MAX_SIZE_CHANGE_PCT * before:
        reasons.append('size_changed_too_much')
    return reasons


def merged_record(shipped: dict, probed: dict, observed: str) -> dict:
    """The shipped record with the probe's identity, size and declared metadata."""
    record = deepcopy(shipped)
    record['source'] = deepcopy(probed['source'])
    record['properties'] = deepcopy(probed['properties'])
    record['acquisition'] = deepcopy(probed['acquisition'])
    for key, value in probed['capabilities'].items():
        if value is not None: record['capabilities'][key] = value
    metadata = record['metadata']
    metadata['checked_at'] = observed
    provenance = deepcopy(metadata.get('provenance') or {})
    provenance['notes'] = ((provenance.get('notes') or '') + ' Re-pinned by NOVA on this computer: '
                           'Ollama re-published the tag after this list was made; identity, size and '
                           'declared metadata come from a registry probe.').strip()
    metadata['provenance'] = provenance
    if 'vision' in record['task_tags'] and record['capabilities'].get('vision') is not True:
        record['task_tags'] = [tag for tag in record['task_tags'] if tag != 'vision']
    if record['capabilities'].get('vision') is True and 'vision' not in record['task_tags']:
        record['task_tags'] = [*record['task_tags'], 'vision']
    return record


def load(store=None) -> tuple[dict, dict]:
    base = Path(store) if store is not None else default_store()
    def read(name, empty):
        try: value = json.loads((base / name).read_bytes())
        except (OSError, ValueError): return deepcopy(empty)
        return value if isinstance(value, dict) else deepcopy(empty)
    return (read('models.json', {'schema_version': 1, 'models': []}),
            read('acquisition_fit.json', {'version': 1, 'source': 'local_show_or_registry_gguf_probe', 'records': []}))


def overlay(records: list[dict], store=None) -> list[dict]:
    """Shipped records with any still-valid re-pin applied; marks them ``repinned``."""
    models, _ = load(store)
    rows = {row.get('record', {}).get('catalog_id'): row for row in models.get('models', [])
            if isinstance(row, dict) and isinstance(row.get('record'), dict)}
    result = []
    for record in records:
        row = rows.get(record.get('catalog_id'))
        pinned = (record.get('acquisition') or {}).get('expected_revision')
        if row and pinned and row.get('repin_of') == pinned:
            result.append(dict(row['record'], repinned={'previous_revision': pinned,
                                                        'observed_at': row.get('observed_at')}))
        else:
            result.append(record)
    return result


def fit_path(store=None) -> Path:
    return (Path(store) if store is not None else default_store()) / 'acquisition_fit.json'


def refresh(shipped: dict, *, fetch=probe_fetch, digest_fetch=manifest_fetch,
            store=None, observed=None) -> dict:
    """Check one shipped record against the registry before an install is proposed.

    Returns ``{"status": ...}``: ``unchanged`` (pin or stored re-pin still current),
    ``repinned`` (new pin stored), ``unchecked`` (registry unreachable or odd: the
    shipped pin is used as before), ``gone`` (tag removed) or ``changed_too_much``
    (with ``reasons``). Never raises for registry problems.
    """
    acquisition = shipped.get('acquisition') or {}
    upstream, pinned = acquisition.get('artifact_id'), acquisition.get('expected_revision')
    if not isinstance(upstream, str) or not isinstance(pinned, str):
        return {'status': 'unchecked', 'reason': 'no_acquisition_pin'}
    status, digest = live_digest(upstream, digest_fetch)
    if status == 'gone': return {'status': 'gone', 'upstream_id': upstream}
    if status != 'ok': return {'status': 'unchecked', 'reason': 'registry_' + status}
    if digest == pinned: return {'status': 'unchanged'}
    current = overlay([shipped], store)[0]
    if current.get('repinned') and current['acquisition'].get('expected_revision') == digest:
        return {'status': 'unchanged', 'repinned': True}
    try:
        found = registry_probe.probe(upstream, fetch=fetch)
    except urllib.error.HTTPError as error:
        return {'status': 'gone', 'upstream_id': upstream} if error.code == 404 else {'status': 'unchecked', 'reason': 'registry_unreachable'}
    except OSError:
        return {'status': 'unchecked', 'reason': 'registry_unreachable'}
    except (ValueError, KeyError, TypeError, struct.error, registry_probe.Truncated):
        return {'status': 'changed_too_much', 'upstream_id': upstream, 'reasons': ['unreadable_model']}
    if observed is None:
        observed = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    probed, fit = registry_probe.records(found, [], None, observed)
    reasons = guard(shipped, probed)
    if reasons:
        return {'status': 'changed_too_much', 'upstream_id': upstream, 'reasons': reasons,
                'pinned_size': acquisition.get('expected_size_bytes'),
                'live_size': probed['properties'].get('weight_size_bytes')}
    record = merged_record(shipped, probed, observed)
    fit['catalog_id'] = record['catalog_id']
    _store(store, {'record': record, 'repin_of': pinned, 'observed_at': observed}, fit)
    return {'status': 'repinned', 'upstream_id': upstream, 'previous_revision': pinned,
            'revision': record['acquisition']['expected_revision'],
            'pinned_size': acquisition.get('expected_size_bytes'),
            'live_size': record['acquisition'].get('expected_size_bytes')}


def _store(store, row: dict, fit: dict) -> None:
    base = Path(store) if store is not None else default_store()
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    models, fits = load(base)
    catalog_id = row['record']['catalog_id']
    models['models'] = [m for m in models.get('models', []) if isinstance(m, dict)
                        and (m.get('record') or {}).get('catalog_id') != catalog_id] + [row]
    fits['records'] = [r for r in fits.get('records', []) if isinstance(r, dict)
                       and r.get('catalog_id') != catalog_id] + [fit]
    for name, doc in (('models.json', models), ('acquisition_fit.json', fits)):
        handle = tempfile.NamedTemporaryFile('w', dir=base, delete=False, prefix='.tmp-repinned-')
        temp = Path(handle.name)
        try:
            with handle:
                json.dump(doc, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write('\n')
            os.chmod(temp, 0o600)
            os.replace(temp, base / name)
        finally:
            if temp.exists(): temp.unlink()
