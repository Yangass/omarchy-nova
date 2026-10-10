"""Bounded read-only integration for the pure onboarding classifier.

The live boundary reads explicit configuration and preserves missing or invalid
configuration as no selection. It reuses one provider inventory/detail observation,
one residency observation, one hardware profile, one catalog snapshot, and one
fit snapshot. Existing recommendation and acquisition policy results are joined
without changing their authority.

Every underlying collector has its own bounded reads/commands. The aggregate
deadline is cooperative: no optional collector starts after it expires, and an
over-budget or changed join is classified as stale. Python threads are not used
because an abandoned collector could continue probing after the response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time

from acquisition_fit import saved_fit
from acquisition_integration import build_preflight, storage_observation
from catalog_observability import catalog_view
from hardware import collect_profile
from model_fit import fit_response
from ollama_readiness import write_readiness
from onboarding import (CandidateSnapshot, InventorySnapshot, OnboardingSnapshot,
                        OperationSnapshot, PrerequisiteSnapshot,
                        RecommendationSnapshot, SelectionSnapshot, classify)
from providers import MIN_OLLAMA_VERSION, ProviderUnavailable
from recommendation_integration import load_and_build_recommendations, parse_request

MAX_CONFIG_BYTES = 4096
MAX_COLLECTION_SECONDS = 30.0
ACTIVE_ACQUISITION_STATES = frozenset((
    'PROPOSED', 'APPROVED', 'STARTING', 'DOWNLOADING', 'VERIFYING',
    'CANCEL_REQUESTED',
))
TERMINAL_ONBOARDING_ACQUISITION_STATES = frozenset((
    'INSTALLED', 'FAILED', 'RECOVERY_REQUIRED', 'CANCELLED', 'REJECTED', 'EXPIRED',
))
BUSY_RUNTIME_STATES = frozenset((
    'loading_model', 'thinking', 'generating', 'tool_call', 'approval_required',
    'executing_action', 'verifying_action',
))


@dataclass(frozen=True)
class ExplicitConfiguration:
    state: str
    provider: str | None = None
    model: str | None = None
    reason: str | None = None
    fingerprint: tuple | None = None

    def public(self):
        return {
            'state': self.state,
            'explicit_selection': self.state == 'VALID',
            'provider': self.provider if self.state == 'VALID' else None,
            'model': self.model if self.state == 'VALID' else None,
            'reason': self.reason,
            'implicit_fallback_enabled': False,
        }


def _file_fingerprint(path):
    try:
        info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode):
            return ('unsafe', info.st_mode)
        return ('file', info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except FileNotFoundError:
        return ('missing',)
    except OSError:
        return None


def inspect_explicit_configuration(path, supported_providers):
    """Read only the two public model-selection fields from a bounded file."""
    path = Path(path)
    fingerprint = _file_fingerprint(path)
    if fingerprint == ('missing',):
        return ExplicitConfiguration('MISSING', reason='configuration_missing',
                                     fingerprint=fingerprint)
    if fingerprint is None:
        return ExplicitConfiguration('UNKNOWN', reason='configuration_unreadable')
    if fingerprint[0] != 'file':
        return ExplicitConfiguration('UNKNOWN', reason='configuration_not_regular',
                                     fingerprint=fingerprint)
    if fingerprint[3] > MAX_CONFIG_BYTES:
        return ExplicitConfiguration('MALFORMED', reason='configuration_too_large',
                                     fingerprint=fingerprint)
    try:
        raw = path.read_bytes()
        if _file_fingerprint(path) != fingerprint:
            return ExplicitConfiguration('UNKNOWN', reason='configuration_changed_during_read')
        value = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return ExplicitConfiguration('MALFORMED', reason='configuration_invalid',
                                     fingerprint=fingerprint)
    if not isinstance(value, dict):
        return ExplicitConfiguration('MALFORMED', reason='configuration_not_object',
                                     fingerprint=fingerprint)
    provider, model = value.get('active_provider'), value.get('active_model')
    if (not isinstance(provider, str) or not provider.strip() or len(provider) > 128
            or not isinstance(model, str) or not model.strip() or len(model) > 512):
        return ExplicitConfiguration('MALFORMED', reason='selection_fields_invalid',
                                     fingerprint=fingerprint)
    if provider not in supported_providers:
        return ExplicitConfiguration('UNSUPPORTED', reason='configured_provider_unsupported',
                                     fingerprint=fingerprint)
    return ExplicitConfiguration('VALID', provider, model, 'explicit_configuration', fingerprint)


def _positive(value):
    return type(value) is int and value > 0


def _hardware_state(profile):
    if not isinstance(profile, dict):
        return 'UNKNOWN'
    capacity = profile.get('capacity')
    if not isinstance(capacity, dict):
        return 'UNAVAILABLE'
    cpu = capacity.get('cpu') if isinstance(capacity.get('cpu'), dict) else {}
    memory = capacity.get('memory') if isinstance(capacity.get('memory'), dict) else {}
    if _positive(cpu.get('logical_cores')) and _positive(memory.get('total_bytes')):
        return 'OBSERVED'
    gpus = capacity.get('gpus') if isinstance(capacity.get('gpus'), list) else []
    if any(value is not None for value in (*cpu.values(), *memory.values())) or gpus:
        return 'PARTIAL'
    return 'UNAVAILABLE'


def _inventory(models, available):
    if available is not True or not isinstance(models, list):
        return InventorySnapshot('UNKNOWN'), False
    identities = []
    malformed = False
    for row in models:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or row.get('installed') is not True or row.get('available') is not True):
            malformed = True
            continue
        identities.append(row['id'])
    duplicates = len(set(identities)) != len(identities)
    return InventorySnapshot('KNOWN' if not malformed else 'UNKNOWN',
                             tuple(sorted(set(identities)))), duplicates or malformed


def _selection(config, inventory, running, runtime_available):
    if config.state != 'VALID':
        return SelectionSnapshot(config.state, None, 'UNKNOWN', None)
    if inventory.state != 'KNOWN':
        return SelectionSnapshot('VALID', config.model, 'UNKNOWN', None)
    matches = [model for model in inventory.installed_model_ids if model == config.model]
    artifact = 'INSTALLED_EXACT' if len(matches) == 1 else 'NOT_INSTALLED'
    if artifact != 'INSTALLED_EXACT' or runtime_available is not True:
        return SelectionSnapshot('VALID', config.model, artifact, None if runtime_available is not True else False)
    resident = any(isinstance(row, dict) and
                   (row.get('name') == config.model or row.get('model') == config.model)
                   for row in running)
    return SelectionSnapshot('VALID', config.model, artifact, resident)


def _recommendation(value):
    if not isinstance(value, dict):
        return RecommendationSnapshot('UNKNOWN'), {'status': 'UNKNOWN', 'candidate_catalog_ids': []}
    status = value.get('status')
    allowed = ('RECOMMENDATION_AVAILABLE', 'SPECIALIST_ONLY',
               'NO_DEFENSIBLE_RECOMMENDATION', 'RECOMMENDATION_UNAVAILABLE')
    if status not in allowed:
        return RecommendationSnapshot('UNKNOWN'), {'status': 'UNKNOWN', 'candidate_catalog_ids': []}
    rows = value.get('tiers', {}).get('recommended', {}).get('candidates', [])
    identities = sorted({row.get('catalog_id') for row in rows
                         if isinstance(row, dict) and isinstance(row.get('catalog_id'), str)})
    # Multiple co-winners are preserved publicly but cannot become one preferred target.
    normalized_status = status if status != 'RECOMMENDATION_AVAILABLE' or len(identities) == 1 \
        else 'NO_DEFENSIBLE_RECOMMENDATION'
    selected = identities[0] if normalized_status == 'RECOMMENDATION_AVAILABLE' else None
    return (RecommendationSnapshot(normalized_status, selected),
            {'status': status, 'candidate_catalog_ids': identities,
             'confidence': value.get('confidence') if isinstance(value.get('confidence'), str) else None})


def _catalog_artifact_state(match):
    status = match.get('match_status') if isinstance(match, dict) else None
    return {
        'exact': 'INSTALLED_EXACT',
        'not_installed': 'NOT_INSTALLED',
        'revision_mismatch': 'INSTALLED_DIFFERENT_REVISION',
        'ambiguous': 'AMBIGUOUS',
    }.get(status, 'UNKNOWN')


def _preflight_candidates(catalog_snapshot, fit_snapshot, disk, writable, saved_fit_loader):
    records = catalog_snapshot.get('models') if isinstance(catalog_snapshot, dict) else None
    if not isinstance(records, list):
        return ()
    candidates = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('catalog_id'), str):
            continue
        match = record.get('installed_match') if isinstance(record.get('installed_match'), dict) else {}
        artifact_state = _catalog_artifact_state(match)
        installed_id = match.get('installed_model_id') if artifact_state == 'INSTALLED_EXACT' else None
        candidate_fit = fit_snapshot
        if artifact_state == 'NOT_INSTALLED':
            fit = saved_fit_loader(record, fit_snapshot)
            if fit:
                source = record.get('source', {})
                candidate_fit = dict(fit_snapshot, models=[*fit_snapshot.get('models', ()), {
                    'provider': source.get('provider'), 'id': source.get('upstream_id'), 'fit': fit,
                }])
        try:
            preflight = build_preflight(catalog_snapshot, candidate_fit,
                                        record['catalog_id'], disk, writable)
        except (ValueError, TypeError, KeyError):
            preflight = None
        status = preflight.get('status') if isinstance(preflight, dict) else 'UNKNOWN'
        if status not in ('ELIGIBLE', 'BLOCKED', 'NO_OP_ALREADY_INSTALLED', 'UNKNOWN'):
            status = 'UNKNOWN'
        blockers = tuple(sorted(value for value in (preflight or {}).get('blockers', ())
                                if isinstance(value, str)))
        candidates.append(CandidateSnapshot(record['catalog_id'], status, artifact_state,
                                            installed_id, blockers))
    return tuple(sorted(candidates, key=lambda value: value.catalog_id))


def _target_from_record(row):
    try:
        value = row['preflight']['descriptor']['catalog_id']
        return value if isinstance(value, str) else None
    except (TypeError, KeyError):
        return None


def _operations(runtime, acquisition_records, selection, installed):
    operations = []
    runtime_state = runtime.get('state') if isinstance(runtime, dict) else None
    if runtime_state == 'model_switching':
        operations.append(OperationSnapshot('ACTIVATION', 'ACTIVATING', runtime.get('model')))
    elif runtime_state == 'model_switch_error':
        operations.append(OperationSnapshot('ACTIVATION', 'FAILED', runtime.get('model')))
    elif runtime_state in BUSY_RUNTIME_STATES:
        operations.append(OperationSnapshot('RUNTIME', 'BUSY'))
    elif runtime_state not in ('idle', 'error'):
        operations.append(OperationSnapshot('RUNTIME', 'UNKNOWN'))

    if acquisition_records is None:
        operations.append(OperationSnapshot('RUNTIME', 'UNKNOWN'))
        return tuple(operations)
    active = [row for row in acquisition_records if isinstance(row, dict)
              and row.get('state') in ACTIVE_ACQUISITION_STATES]
    for row in active:
        operations.append(OperationSnapshot('ACQUISITION', row['state'], _target_from_record(row)))
    if not active and (selection.config_state != 'VALID' or not installed):
        terminal = next((row for row in acquisition_records if isinstance(row, dict)
                         and row.get('state') in TERMINAL_ONBOARDING_ACQUISITION_STATES), None)
        if terminal:
            operations.append(OperationSnapshot('ACQUISITION', terminal['state'],
                                                _target_from_record(terminal)))
    return tuple(operations)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def collect_onboarding_state(*, config_path, providers, default_provider,
                             effective_selection_reader, runtime_reader,
                             acquisition_reader, max_seconds=MAX_COLLECTION_SECONDS,
                             clock=None, profile_collector=None, catalog_collector=None,
                             recommendation_loader=None, storage_collector=None,
                             readiness_collector=None, saved_fit_loader=None):
    """Collect one advisory snapshot; all mutating methods remain unreachable."""
    if type(max_seconds) not in (int, float) or isinstance(max_seconds, bool) or max_seconds <= 0:
        raise ValueError('max_seconds must be positive')
    clock = clock or time.monotonic
    profile_collector = profile_collector or collect_profile
    catalog_collector = catalog_collector or catalog_view
    recommendation_loader = recommendation_loader or load_and_build_recommendations
    storage_collector = storage_collector or storage_observation
    readiness_collector = readiness_collector or write_readiness
    saved_fit_loader = saved_fit_loader or saved_fit
    started = clock()
    deadline = started + max_seconds
    runtime_before = runtime_reader()
    effective_before = effective_selection_reader()
    config = inspect_explicit_configuration(config_path, frozenset(providers))

    try:
        acquisition_records = acquisition_reader()
    except Exception:
        acquisition_records = None

    adapter = providers.get(config.provider if config.state == 'VALID' else default_provider)
    provider_state, models, running, runtime_available = 'UNSUPPORTED', None, [], False
    provider_version = None
    if adapter is not None:
        try:
            models = adapter.fit_models()
            provider_state = 'AVAILABLE'
        except ProviderUnavailable:
            provider_state = 'UNAVAILABLE'
        except (OSError, ValueError, TypeError, KeyError):
            provider_state = 'UNKNOWN'
        # Below the minimum the provider needs updating. An unreadable version is
        # only reported: blocking a working setup on one failed read is worse.
        if provider_state == 'AVAILABLE' and callable(getattr(adapter, 'version', None)):
            try:
                provider_version = adapter.version()
            except (OSError, ValueError, TypeError, KeyError):
                provider_version = None
            if provider_version is not None and provider_version < MIN_OLLAMA_VERSION:
                provider_state, models = 'OUTDATED', None
        if provider_state == 'AVAILABLE' and clock() <= deadline:
            try:
                running = adapter.running_models()
                runtime_available = isinstance(running, list)
            except ProviderUnavailable:
                runtime_available = False
            except (OSError, ValueError, TypeError, KeyError):
                running, runtime_available = [], False

    inventory, inventory_conflict = _inventory(models, provider_state == 'AVAILABLE')
    selection = _selection(config, inventory, running, runtime_available)

    profile = None
    if clock() <= deadline:
        try:
            profile = profile_collector(provider_state == 'AVAILABLE')
        except (OSError, ValueError, TypeError, KeyError):
            profile = None
    hardware_state = _hardware_state(profile)
    if isinstance(profile, dict):
        try:
            fit_snapshot = fit_response(profile, models or [], running, runtime_available)
        except (OSError, ValueError, TypeError, KeyError):
            fit_snapshot = {'provider_available': provider_state == 'AVAILABLE', 'models': [],
                            'error': 'fit_snapshot_unavailable'}
    else:
        fit_snapshot = {'provider_available': provider_state == 'AVAILABLE', 'models': [],
                        'error': 'hardware_profile_unavailable'}

    if clock() <= deadline:
        try:
            catalog_snapshot = catalog_collector(include_models=True)
        except (OSError, ValueError, TypeError, KeyError):
            catalog_snapshot = {'status': 'unavailable', 'catalog_revision': None, 'models': []}
    else:
        catalog_snapshot = {'status': 'unavailable', 'catalog_revision': None, 'models': []}

    if clock() <= deadline:
        try:
            recommendation_value = recommendation_loader(
                catalog_snapshot, fit_snapshot, parse_request(''))
        except (OSError, ValueError, TypeError, KeyError):
            recommendation_value = {'status': 'RECOMMENDATION_UNAVAILABLE'}
    else:
        recommendation_value = {'status': 'RECOMMENDATION_UNAVAILABLE'}
    recommendation, recommendation_public = _recommendation(recommendation_value)

    if clock() <= deadline:
        try:
            disk, _unused = storage_collector()
            writable = readiness_collector()
            candidates = _preflight_candidates(catalog_snapshot, fit_snapshot, disk, writable,
                                               saved_fit_loader)
        except (OSError, ValueError, TypeError, KeyError):
            candidates = ()
    else:
        candidates = ()

    runtime_after = runtime_reader()
    effective_after = effective_selection_reader()
    finished = clock()
    consistency = 'CURRENT'
    consistency_reasons = []
    if inventory_conflict:
        consistency, consistency_reasons = 'CONFLICTING', ['inventory_malformed_or_duplicate']
    if runtime_before != runtime_after or effective_before != effective_after:
        consistency = 'STALE'
        consistency_reasons.append('runtime_or_effective_selection_changed')
    expected_effective = (config.provider, config.model) if config.state == 'VALID' else None
    if expected_effective is not None and effective_after != expected_effective:
        consistency = 'CONFLICTING'
        consistency_reasons.append('explicit_and_effective_selection_disagree')
    if _file_fingerprint(config_path) != config.fingerprint:
        consistency = 'STALE'
        consistency_reasons.append('configuration_changed')
    if finished > deadline:
        consistency = 'STALE'
        consistency_reasons.append('collection_budget_exceeded')

    operations = _operations(runtime_after, acquisition_records, selection,
                             inventory.installed_model_ids)
    normalized = OnboardingSnapshot(
        prerequisites=PrerequisiteSnapshot('AVAILABLE', provider_state, hardware_state),
        inventory=inventory, selection=selection, candidates=candidates,
        recommendation=recommendation, operations=operations,
        snapshot_consistency=consistency,
    )
    result = classify(normalized).as_dict()
    result.update({
        'schema_version': 1,
        'observed_at': _now(),
        'configuration': config.public(),
        'observations': {
            'provider': {'id': config.provider if config.state == 'VALID' else default_provider,
                         'state': provider_state,
                         'version': '.'.join(map(str, provider_version)) if provider_version else None,
                         'minimum_version': '.'.join(map(str, MIN_OLLAMA_VERSION))},
            'hardware': {'state': hardware_state},
            'inventory': {'state': inventory.state,
                          'installed_model_ids': list(inventory.installed_model_ids)},
            'runtime': {'state': runtime_after.get('state') if isinstance(runtime_after, dict) else None},
            'catalog': {'state': catalog_snapshot.get('status') if isinstance(catalog_snapshot, dict) else None,
                        'revision': catalog_snapshot.get('catalog_revision') if isinstance(catalog_snapshot, dict) else None},
            'recommendation': recommendation_public,
            'acquisition_state': 'AVAILABLE' if acquisition_records is not None else 'UNKNOWN',
        },
        'collection': {
            'status': 'CURRENT' if consistency == 'CURRENT' else consistency,
            'duration_ms': max(0, int((finished - started) * 1000)),
            'budget_ms': int(max_seconds * 1000),
            'reasons': consistency_reasons,
        },
        'advisory': True,
        'action_authority': 'Existing endpoint validation, approval, acquisition, and activation policies remain authoritative.',
    })
    return result
