"""Tell the user when Ollama has a newer build of a model they already installed.

NOVA never updates on its own. Once a week (``due``), or when the user clicks
REFRESH, ``check_all`` reads the live registry manifest of every installed model
NOVA knows (shipped catalog or ADD A MODEL). A newer build is offered only when it
is still the same model (``catalog_repins.guard``: architecture family,
quantization, size within 25%); otherwise the result says why it is not offered.

What changed comes from comparing the local manifest's layers with the live ones:
same weights layer means only the chat template or settings changed, and the
download is exactly the layers this computer does not have yet.

Results live in ``$XDG_STATE_HOME/nova/model-updates.json`` (0600) together with
the builds the user chose to skip. Every registry read goes through an injectable
``fetch`` (tests pass a fake); no network access happens here otherwise.
"""
from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import catalog_drift
import catalog_repins
import registry_probe
from catalog_observability import OLLAMA_STORAGE, _read_manifest

WEEK_SECONDS = 7 * 24 * 3600
RETRY_SECONDS = 24 * 3600  # after a check that could not reach the registry
INSTALLED = ('exact', 'revision_mismatch')


def default_path() -> Path:
    base = os.environ.get('XDG_STATE_HOME')
    root = Path(base).expanduser() if base else Path.home() / '.local' / 'state'
    return root / 'nova' / 'model-updates.json'


def empty_state() -> dict:
    return {'version': 1, 'checked_at': None, 'failed': False, 'results': {}, 'skipped': {}}


def load(path=None) -> dict:
    try: value = json.loads(Path(path or default_path()).read_bytes())
    except (OSError, ValueError): return empty_state()
    if not isinstance(value, dict) or value.get('version') != 1: return empty_state()
    state = empty_state()
    for key in ('results', 'skipped'):
        if isinstance(value.get(key), dict): state[key] = value[key]
    if type(value.get('checked_at')) in (int, float): state['checked_at'] = value['checked_at']
    state['failed'] = value.get('failed') is True
    return state


def save(state: dict, path=None) -> None:
    target = Path(path or default_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=target.parent, delete=False, prefix='.tmp-model-updates-')
    temp = Path(handle.name)
    try:
        with handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write('\n')
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    finally:
        if temp.exists(): temp.unlink()


def due(state: dict, now: float) -> bool:
    checked = state.get('checked_at')
    if not isinstance(checked, (int, float)) or checked > now: return True
    return now - checked >= (RETRY_SECONDS if state.get('failed') else WEEK_SECONDS)


def local_layers(artifact_id: str, root=OLLAMA_STORAGE) -> dict | None:
    """{digest: (media kind, size)} of the installed manifest, or None when unreadable."""
    # Same identities the download adapter accepts: never a path (security review 2026-10-11).
    if not isinstance(artifact_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}', artifact_id) \
            or '..' in artifact_id:
        return None
    try:
        name, tag = artifact_id.split(':')
        repo = 'library/' + name
        path = Path(root) / 'manifests' / 'registry.ollama.ai' / repo / tag
        _read_manifest(path, Path(root))
        manifest = json.loads(path.read_bytes())
        layers = [*manifest['layers'], manifest['config']]
        return {layer['digest']: (str(layer.get('mediaType', '')).rsplit('.', 1)[-1], int(layer['size'])) for layer in layers}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _live_manifest(upstream: str, digest_fetch):
    """manifest_check plus the raw manifest it read (for the layer list)."""
    seen = {}
    def recording(url, headers=None):
        seen['raw'] = digest_fetch(url, headers)
        return seen['raw']
    check = catalog_drift.manifest_check(upstream, fetch=recording)
    layers = None
    if check['status'] == 'ok':
        manifest = json.loads(seen['raw'])
        layers = {layer['digest']: (str(layer.get('mediaType', '')).rsplit('.', 1)[-1], layer['size'])
                  for layer in [*manifest['layers'], manifest['config']]}
    return check, layers


def check_record(record: dict, *, fetch=catalog_repins.probe_fetch, digest_fetch=catalog_repins.manifest_fetch,
                 root=OLLAMA_STORAGE, observed=None) -> dict:
    """One installed model against the registry. Never raises for registry problems."""
    upstream = catalog_drift.upstream_id_of(record)
    installed = (record.get('installed_match') or {}).get('installed_digest')
    base = {'catalog_id': record.get('catalog_id'), 'model_id': upstream}
    if not isinstance(upstream, str) or not isinstance(installed, str):
        return {**base, 'status': 'unchecked', 'reason': 'no_installed_revision'}
    check, live = _live_manifest(upstream, digest_fetch)
    if check['status'] == 'gone': return {**base, 'status': 'gone'}
    if check['status'] != 'ok': return {**base, 'status': 'unchecked', 'reason': 'registry_' + check['status']}
    if check['manifest_sha256'] == installed: return {**base, 'status': 'current', 'revision': installed}
    local = local_layers(upstream, root)
    local_weights = next((d for d, (kind, _) in local.items() if kind == 'model'), None) if local else None
    same_weights = local_weights is not None and local_weights == check['model_layer_digest']
    if not same_weights:
        try:
            found = registry_probe.probe(upstream, fetch=fetch)
        except urllib.error.HTTPError as error:
            return {**base, 'status': 'gone'} if error.code == 404 else {**base, 'status': 'unchecked', 'reason': 'registry_unreachable'}
        except OSError:
            return {**base, 'status': 'unchecked', 'reason': 'registry_unreachable'}
        except (ValueError, KeyError, TypeError, struct.error, registry_probe.Truncated):
            return {**base, 'status': 'changed_too_much', 'reasons': ['unreadable_model']}
        stamp = observed or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        probed, _ = registry_probe.records(found, [], None, stamp)
        reasons = catalog_repins.guard(record, probed)
        if reasons: return {**base, 'status': 'changed_too_much', 'reasons': reasons}
    size_before = sum(size for _, size in local.values()) if local else (record.get('acquisition') or {}).get('expected_size_bytes')
    return {**base, 'status': 'available', 'kind': 'settings' if same_weights else 'weights',
            'revision': check['manifest_sha256'], 'installed_revision': installed,
            'download_bytes': sum(size for digest, (_, size) in live.items() if digest not in local) if local else check['total_size'],
            'download_exact': local is not None,
            'size_before': size_before, 'size_after': check['total_size']}


def check_all(records: list[dict], *, path=None, now=None, **kwargs) -> dict:
    """Check every installed model NOVA knows; store and return the new state."""
    state = load(path)
    results = {}
    for record in records:
        if (record.get('installed_match') or {}).get('match_status') in INSTALLED:
            result = check_record(record, **kwargs)
            results[record['catalog_id']] = result
    reached = [r for r in results.values() if r['status'] != 'unchecked']
    state.update(checked_at=now if now is not None else time.time(), results=results,
                 failed=bool(results) and not reached)
    # A skip only hides the build it was made for.
    state['skipped'] = {k: v for k, v in state['skipped'].items() if (results.get(k) or {}).get('revision') == v}
    save(state, path)
    return state


def view(state: dict, records: list[dict]) -> dict:
    """What the panel shows: results keyed by installed model id, adjusted to what is on disk now."""
    installed = {r['catalog_id']: (r.get('installed_match') or {}) for r in records}
    models = {}
    for catalog_id, result in state.get('results', {}).items():
        match = installed.get(catalog_id) or {}
        if match.get('match_status') not in INSTALLED or not isinstance(result, dict): continue
        item = dict(result)
        if item.get('status') == 'available' and match.get('installed_digest') == item.get('revision'):
            item = {'catalog_id': catalog_id, 'model_id': item.get('model_id'), 'status': 'current', 'revision': item['revision']}
        item['skipped'] = item.get('status') == 'available' and state.get('skipped', {}).get(catalog_id) == item.get('revision')
        models[item.get('model_id') or catalog_id] = item
    available = sum(1 for item in models.values() if item['status'] == 'available' and not item['skipped'])
    return {'checked_at': state.get('checked_at'), 'failed': state.get('failed', False),
            'available': available, 'models': models}


def set_skipped(catalog_id: str, skip: bool, path=None) -> dict | None:
    """Skip (or show again) the newer build currently offered for one model."""
    state = load(path)
    result = state['results'].get(catalog_id)
    if skip:
        if not isinstance(result, dict) or result.get('status') != 'available': return None
        state['skipped'][catalog_id] = result['revision']
    else:
        state['skipped'].pop(catalog_id, None)
    save(state, path)
    return state
