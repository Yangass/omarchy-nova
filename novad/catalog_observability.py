"""Read-only catalog presentation and local manifest inventory; no network APIs."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from catalog import Catalog, DEFAULT_PATH, SCHEMA_VERSION, load_catalog, validate_catalog
import catalog_repins
import requested_models

# Verified deployment setting; override for other locally managed installations.
OLLAMA_STORAGE = Path(os.environ.get('NOVA_OLLAMA_MODELS', '/var/lib/ollama'))
MAX_MANIFESTS = 512
MAX_MANIFEST_BYTES = 262144
MAX_LAYERS = 128
STATES = ('exact', 'not_installed', 'revision_mismatch', 'ambiguous', 'unknown')


# Ollama 0.40 (2026-10) keeps manifests in a second tree: manifests-v2/<host>/<namespace>/
# <model>/<tag> is a link to the manifest's own blob (named by its sha256), and pulls still
# write the old tree with the real manifest added as one extra layer of this type. Models
# made with `ollama create` or `cp` (NOVA's coding-CLI builds) exist only in the new tree.
MANIFEST_LAYER = 'application/vnd.docker.distribution.manifest.v2+json'
TREES = (('manifests-v2', 'ollama.com', True), ('manifests', 'registry.ollama.ai', False))


def _manifest_blob(root, hexdigest):
    """A manifest stored as a blob: a regular file whose sha256 is its name."""
    path = root / 'blobs' / ('sha256-' + hexdigest)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file: raw = file.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES: raise ValueError('manifest size limit')
    if hashlib.sha256(raw).hexdigest() != hexdigest: raise ValueError('manifest blob digest mismatch')
    return raw


def _check_manifest(raw, root):
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 2:
        raise ValueError('unsupported manifest')
    layers = value.get('layers')
    if not isinstance(layers, list) or not layers or len(layers) > MAX_LAYERS:
        raise ValueError('invalid manifest layers')
    for layer in [value.get('config')] + layers:
        if not isinstance(layer, dict): raise ValueError('invalid blob reference')
        digest, size = layer.get('digest'), layer.get('size')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('invalid blob digest')
        if type(size) is not int or size < 0: raise ValueError('invalid blob size')
        blob = root / 'blobs' / digest.replace(':', '-')
        if blob.is_symlink() or not blob.is_file() or blob.stat().st_size != size:
            raise ValueError('referenced blob unavailable or size mismatch')
    return value


def read_manifest(path, root, linked=False):
    """(digest, manifest) of one installed model, in either Ollama layout. The digest is the
    one Ollama reports (/api/tags) and the registry pins: the sha256 of the real manifest.
    Links are followed only in the new tree (`linked`), and only to a manifest blob."""
    if linked and path.is_symlink():
        match = re.fullmatch(r'sha256-([0-9a-f]{64})', os.path.basename(os.readlink(path)))
        if not match or path.resolve().parent != (root / 'blobs').resolve(): raise ValueError('unexpected manifest link')
        raw = _manifest_blob(root, match.group(1))
    else:
        if path.is_symlink(): raise ValueError('symlink manifest not inspected')
        with path.open('rb') as file: raw = file.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES: raise ValueError('manifest size limit')
    value = _check_manifest(raw, root)
    wrapped = [layer for layer in value['layers'] if layer.get('mediaType') == MANIFEST_LAYER]
    if wrapped:
        if linked or len(wrapped) != 1: raise ValueError('unexpected manifest layer')
        raw = _manifest_blob(root, wrapped[0]['digest'][len('sha256:'):])
        value = _check_manifest(raw, root)
        if any(layer.get('mediaType') == MANIFEST_LAYER for layer in value['layers']): raise ValueError('unexpected manifest layer')
    return hashlib.sha256(raw).hexdigest(), value


def _read_manifest(path, root, linked=False):
    return read_manifest(path, root, linked)[0]


def manifest_path(root, name, tag):
    """(path, linked) of a library model's manifest: the new tree when Ollama wrote one."""
    root = Path(root)
    new = root / 'manifests-v2' / 'ollama.com' / 'library' / name / tag
    return (new, True) if os.path.lexists(new) else (root / 'manifests' / 'registry.ollama.ai' / 'library' / name / tag, False)


def local_inventory(root=OLLAMA_STORAGE, with_layers=False):
    """Read manifests + stat referenced blobs. Never hash large weights or call Ollama.

    ``with_layers`` adds each model's files as ``layers`` ({blob digest: size}), so a
    delete can say what it really frees and which models share those files.

    Both of Ollama's trees are read; a model in the new tree is not read again from the old.
    Partial scans never establish absence. Unexpected layouts are not guessed.
    """
    root = Path(root)
    observed_at = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    result = {'provider': 'ollama', 'source': 'local_ollama_manifests', 'status': 'ready',
              'observed_at': observed_at, 'records': [], 'errors': [],
              'note': 'Filesystem snapshot; no provider availability, residency or blob-content integrity check.'}
    if not (root / 'manifests').is_dir() or (root / 'manifests').is_symlink() or (root/'blobs').is_symlink() \
            or (root / 'manifests-v2').is_symlink():
        result.update(status='unavailable', errors=['local manifest storage unavailable'])
        return result
    count, seen = 0, set()
    def scan_error(_error): result['errors'].append('manifest directory unreadable')
    for tree, default_host, linked in TREES:
        manifest_root = root / tree
        if linked and not manifest_root.is_dir(): continue  # Ollama before 0.40
        for directory, dirs, files in os.walk(manifest_root, onerror=scan_error, followlinks=False):
            base = Path(directory)
            relative = base.relative_to(manifest_root).parts
            for name in list(dirs):
                if (base/name).is_symlink():
                    dirs.remove(name); result['errors'].append('symlink directory not inspected')
            if len(relative) >= 3:
                if dirs: result['errors'].append('unexpected manifest directory depth')
                dirs[:] = []
            for filename in files:
                count += 1
                if count > MAX_MANIFESTS:
                    result.update(status='partial'); result['errors'].append('manifest count limit')
                    return result
                if len(relative) != 3:
                    result['errors'].append('unexpected manifest layout'); continue
                registry, namespace, model = relative
                # Ollama's default registry/library may be omitted in a model tag.
                prefix = '' if registry == default_host else registry + '/'
                prefix += '' if registry == default_host and namespace == 'library' else namespace + '/'
                identifier = prefix + model + ':' + filename
                if identifier in seen: continue
                seen.add(identifier)
                try: digest, manifest = read_manifest(base/filename, root, linked)
                except (OSError, ValueError, RecursionError):
                    result['errors'].append('manifest unreadable/invalid or referenced blobs incomplete')
                    result['records'].append({'provider': 'ollama', 'id': identifier, 'installed': None, 'digest': None})
                    continue
                record = {'provider': 'ollama', 'id': identifier, 'installed': True, 'digest': digest}
                if with_layers:
                    record['layers'] = {layer['digest']: layer['size'] for layer in [manifest['config'], *manifest['layers']]}
                result['records'].append(record)
    if result['errors']: result['status'] = 'partial'
    return result


def present_catalog(catalog, inventory, include_models=True):
    """Add a separate observation to each record; no catalog or inventory mutation."""
    records = inventory['records']
    joins = catalog.match_installed(records)  # Reuse v0.10A exact identity/revision logic.
    counts = dict.fromkeys(STATES, 0)
    output = []
    checked_dates = []
    for source_record in catalog.records:
        record = deepcopy(source_record)
        source = record['source']
        provider, identifier = source['provider'], source['upstream_id']
        candidates = [r for r in records if r.get('provider') == provider and r.get('id') == identifier]
        matching = [j for j in joins if j['catalog_id'] == record['catalog_id']]
        observation = {'installed': None, 'installed_model_id': None, 'match_status': 'unknown',
                       'catalog_digest': source['revision_digest'], 'installed_digest': None,
                       'revision_match': None, 'reason': 'inventory_unavailable_or_incomplete'}
        if len(candidates) > 1:
            observation.update(match_status='ambiguous', reason='multiple_inventory_entries_for_identity')
        elif len(candidates) == 1 and candidates[0].get('installed') is True and len(matching) == 1:
            join = matching[0]
            revision = join['revision_match']
            observation.update(installed=True, installed_model_id=identifier,
                               installed_digest=candidates[0].get('digest'), revision_match=revision,
                               match_status='exact' if revision is True else 'revision_mismatch' if revision is False else 'unknown',
                               reason='identifier_and_digest_match' if revision is True else 'digest_differs' if revision is False else 'revision_evidence_missing')
        elif not candidates and provider == inventory['provider'] and inventory['status'] == 'ready':
            observation.update(installed=False, match_status='not_installed', reason='absent_from_complete_local_inventory')
        counts[observation['match_status']] += 1
        record['installed_match'] = observation
        checked = record['metadata']['checked_at']
        if checked: checked_dates.append(checked)
        record['freshness'] = {'catalog_observed_at': checked, 'assessment': 'unknown',
                               'note': 'Recorded catalog observation only; no upstream, quality or artifact-freshness verification.'}
        output.append(record)
    response = {'schema_version': SCHEMA_VERSION if catalog.status in ('ready', 'partial') else None,
                'supported_schema_version': SCHEMA_VERSION, 'catalog_revision': catalog.revision,
                'status': catalog.status, 'record_count': len(output), 'loader_errors': list(catalog.errors),
                'installed_match_counts': counts,
                'freshness': {'oldest_record_checked_at': min(checked_dates, default=None),
                              'latest_record_checked_at': max(checked_dates, default=None),
                              'records_with_timestamp': len(checked_dates), 'automatic_refresh': False},
                'inventory': {k: deepcopy(v) for k, v in inventory.items() if k != 'records'},
                'provenance_note': 'Provenance is preserved as recorded; the local seed is not external benchmark or upstream verification.'}
    if include_models: response['models'] = output
    return response


def _requested_models_doc() -> dict:
    """The user-requested store's models doc (QWEN-08); empty doc when absent or unreadable.

    Read in binary to honor the read-only diagnostics' ``rb``-only open contract.
    """
    path = requested_models.default_store() / 'models.json'
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        value = None
    return value if isinstance(value, dict) else {'schema_version': 1, 'catalog_revision': 'requested', 'models': []}


def shipped_with_repins(catalog):
    """Shipped records, each replaced by its still-valid local re-pin when that re-pin validates."""
    result = []
    for shipped, record in zip(catalog.records, catalog_repins.overlay(list(catalog.records))):
        if record is not shipped:
            checked = validate_catalog({'schema_version': SCHEMA_VERSION, 'catalog_revision': catalog.revision or 'repinned',
                                        'models': [{k: v for k, v in record.items() if k != 'repinned'}]})
            record = dict(checked.records[0], repinned=record['repinned']) if checked.status == 'ready' else shipped
        result.append(record)
    return result


def catalog_view(include_models=True):
    catalog = load_catalog(DEFAULT_PATH)
    records = [dict(record, user_requested=False) for record in shipped_with_repins(catalog)]
    seen = {record['catalog_id'] for record in records}
    # A request for a model the catalog already ships would only duplicate its row.
    shipped = {record['source'].get('upstream_id') for record in records}
    for record in validate_catalog(_requested_models_doc()).records:
        if record['catalog_id'] not in seen and record['source'].get('upstream_id') not in shipped:
            seen.add(record['catalog_id'])
            records.append(dict(record, user_requested=True))
    return present_catalog(Catalog(catalog.status, catalog.revision, tuple(records), catalog.errors),
                           local_inventory(), include_models)
