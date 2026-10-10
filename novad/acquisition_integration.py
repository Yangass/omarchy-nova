"""Read-only catalog/fit/storage normalization for acquisition preflight.

The pure policy remains in acquisition.py. This boundary may read existing local
snapshots and filesystem capacity but never calls a provider mutation method,
creates approvals, or changes model/runtime state.
"""
from __future__ import annotations

import os
from pathlib import Path

from acquisition import (AcquisitionDescriptor, DiskSnapshot, FitSnapshot,
                         InventorySnapshot, PreflightRequest, ProviderPreflight,
                         descriptor_from_catalog, evaluate_preflight)
from catalog_observability import OLLAMA_STORAGE
from acquisition_fit import saved_fit
from ollama_readiness import write_readiness
from providers import parse_version
import re

# Backend-owned policy, not a catalog-provided trust assertion or endpoint input.
TRUSTED_ACQUISITION_SOURCES = {'ollama': frozenset(('ollama_registry', 'ollama_registry_requested'))}


def _bool(value):
    return value if type(value) is bool else None


def trust_state(record):
    """Resolve source trust exclusively against the backend allowlist."""
    acquisition = record.get('acquisition') if isinstance(record.get('acquisition'), dict) else None
    if acquisition is None:
        return 'UNVERIFIED'
    provider = acquisition.get('provider')
    source = acquisition.get('source')
    if not isinstance(provider, str) or not isinstance(source, str):
        return 'UNKNOWN'
    if provider not in TRUSTED_ACQUISITION_SOURCES:
        return 'UNSUPPORTED'
    if source not in TRUSTED_ACQUISITION_SOURCES[provider]:
        return 'UNVERIFIED'
    artifact = acquisition.get('artifact_id')
    if not isinstance(artifact, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}', artifact):
        return 'UNVERIFIED'
    if 'cloud' in artifact.split(':')[1]: return 'UNSUPPORTED'
    return 'TRUSTED'


def storage_observation(root=OLLAMA_STORAGE):
    """Observe only the configured provider storage filesystem; never probe writes."""
    root = Path(root)
    try:
        if root.is_symlink() or not root.is_dir():
            return DiskSnapshot(None, None), None
        stat = os.statvfs(root)
        total, free = stat.f_frsize * stat.f_blocks, stat.f_frsize * stat.f_bavail
        if total <= 0 or free < 0:
            return DiskSnapshot(None, None), None
        # Provider-user write authority cannot be demonstrated without a write.
        return DiskSnapshot(total, free), None
    except OSError:
        return DiskSnapshot(None, None), None


def _inventory(record):
    observed = record.get('installed_match') if isinstance(record.get('installed_match'), dict) else {}
    mapping = {'exact': 'INSTALLED_EXACT', 'not_installed': 'NOT_INSTALLED',
               'revision_mismatch': 'INSTALLED_DIFFERENT_REVISION', 'ambiguous': 'AMBIGUOUS'}
    return InventorySnapshot(mapping.get(observed.get('match_status'), 'UNKNOWN'),
                             observed.get('installed_digest') if isinstance(observed.get('installed_digest'), str) else None)


def _fit(record, fit_snapshot):
    source = record.get('source') if isinstance(record.get('source'), dict) else {}
    matches = [item for item in fit_snapshot.get('models', ()) if isinstance(item, dict) and
               item.get('provider') == source.get('provider') and item.get('id') == source.get('upstream_id')]
    if len(matches) != 1:
        return FitSnapshot('UNKNOWN')
    value = matches[0].get('fit') if isinstance(matches[0].get('fit'), dict) else {}
    state = value.get('rating')
    return FitSnapshot(state if state in ('EXCELLENT', 'GOOD', 'TIGHT', 'POOR', 'UNSUPPORTED', 'UNKNOWN') else 'UNKNOWN')


def _dotted(value):
    return None if value is None else f'{value[0]}.{value[1]}.{value[2]}'


def build_preflight(catalog_snapshot, fit_snapshot, catalog_id, disk_snapshot, writable=None, provider_version=None):
    """Pure snapshot join used by tests and the live read-only adapter."""
    if not isinstance(catalog_id, str) or not catalog_id:
        raise ValueError('invalid catalog identity')
    if not isinstance(catalog_snapshot, dict) or not isinstance(fit_snapshot, dict):
        raise ValueError('invalid acquisition snapshots')
    records = catalog_snapshot.get('models') if isinstance(catalog_snapshot.get('models'), list) else []
    record = next((item for item in records if isinstance(item, dict) and item.get('catalog_id') == catalog_id), None)
    if record is None:
        return None
    trust = trust_state(record)
    descriptor = descriptor_from_catalog(record, catalog_snapshot.get('catalog_revision'), trust)
    provider_id = descriptor.provider
    supported = provider_id == 'ollama' if provider_id is not None else None
    available = _bool(fit_snapshot.get('provider_available')) if supported is True else None
    acquisition = record.get('acquisition') if isinstance(record.get('acquisition'), dict) else None
    minimum = parse_version(acquisition.get('minimum_provider_version')) if acquisition else None
    result = evaluate_preflight(
        descriptor, _inventory(record), _fit(record, fit_snapshot), disk_snapshot,
        ProviderPreflight(supported, available, writable, version=provider_version, minimum_version=minimum),
        PreflightRequest(catalog_snapshot.get('catalog_revision')))
    result['catalog_revision_used'] = catalog_snapshot.get('catalog_revision')
    result['acquisition_source'] = acquisition.get('source') if acquisition else None
    if trust == 'TRUSTED' and result['acquisition_source'] == 'ollama_registry_requested':
        result['trust_basis'] = 'user_requested'
    else:
        result['trust_basis'] = 'backend_allowlist' if trust == 'TRUSTED' else 'not_trusted_by_backend_allowlist'
    result['provider_version'] = {'installed': _dotted(provider_version), 'required': _dotted(minimum)}
    return result


def live_preflight(catalog_snapshot, fit_snapshot, catalog_id, provider_version=None):
    """Use only current read-only snapshots and configured storage capacity."""
    disk, writable = storage_observation()
    writable = write_readiness()
    # Read-only stored metadata is used only for an explicitly absent exact artifact.
    record = next((r for r in catalog_snapshot.get('models', []) if r['catalog_id'] == catalog_id), None)
    fit = None
    if record and record.get('installed_match', {}).get('match_status') == 'not_installed':
        fit = saved_fit(record, fit_snapshot)
        if fit:
            fit_snapshot = dict(fit_snapshot, models=[*fit_snapshot.get('models', []),
                {'provider': record['source']['provider'], 'id': record['source']['upstream_id'], 'fit': fit}])
    result = build_preflight(catalog_snapshot, fit_snapshot, catalog_id, disk, writable,
                             provider_version=provider_version)
    if result:
        result['readiness_evidence'] = 'audited_systemd_preset_process_identity_owner_permissions' if writable is True else 'unproven_or_not_writable'
        result['fit_evidence'] = 'revision_bound_local_metadata' if record and record.get('installed_match', {}).get('match_status') == 'not_installed' else 'installed_provider_metadata'
        # The same estimate the fit state came from, so the UI can explain it.
        result['fit_estimate'] = ({'rating': fit.get('rating'), 'recommended_context': fit.get('recommended_context'),
                                   'source': 'catalog_metadata_estimate'} if isinstance(fit, dict) else None)
    return result
