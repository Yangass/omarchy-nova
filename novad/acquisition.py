"""Pure trusted-acquisition descriptor validation and preflight policy.

This module has no catalog loader, provider, server, filesystem, network, clock,
or runtime imports. Callers supply normalized snapshots. It cannot acquire,
approve, switch, or mutate anything; see ACQUISITION_POLICY.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

GIB = 1024 ** 3
TRUST_STATES = ('TRUSTED', 'UNVERIFIED', 'UNSUPPORTED', 'UNKNOWN')
INVENTORY_STATES = ('NOT_INSTALLED', 'INSTALLED_EXACT', 'INSTALLED_DIFFERENT_REVISION',
                    'AMBIGUOUS', 'UNKNOWN')
FIT_STATES = ('EXCELLENT', 'GOOD', 'TIGHT', 'POOR', 'UNSUPPORTED', 'UNKNOWN')


class InputError(ValueError):
    """A caller supplied malformed normalized input."""


@dataclass(frozen=True)
class AcquisitionStoragePolicy:
    transient_min_bytes: int = 2 * GIB
    transient_fraction_numerator: int = 25
    transient_fraction_denominator: int = 100
    safety_min_bytes: int = 4 * GIB
    safety_fraction_numerator: int = 5
    safety_fraction_denominator: int = 100


@dataclass(frozen=True)
class AcquisitionDescriptor:
    catalog_id: str
    catalog_revision: str | None
    provider: str | None
    source_trust_state: str
    artifact_id: str | None
    exact_tag: str | None
    expected_revision: str | None
    expected_artifact_size: int | None
    provenance_summary: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventorySnapshot:
    state: str
    observed_revision: str | None = None


@dataclass(frozen=True)
class FitSnapshot:
    state: str
    unknown_fit_qualified: bool = False


@dataclass(frozen=True)
class DiskSnapshot:
    filesystem_total: int | None
    filesystem_free: int | None


@dataclass(frozen=True)
class ProviderPreflight:
    supported: bool | None
    available: bool | None
    writable: bool | None
    acquisition_in_progress: bool | None = False
    previous_failure: bool | None = False
    version: tuple[int, int, int] | None = None
    minimum_version: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class PreflightRequest:
    current_catalog_revision: str | None
    allow_tight: bool = False


def _text(value, name, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise InputError(f'{name}: expected bounded nonempty string' + (' or null' if nullable else ''))


def _bytes(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise InputError(f'{name}: expected non-negative integer bytes or null')


def _bool_or_none(value, name):
    if value is not None and type(value) is not bool:
        raise InputError(f'{name}: expected bool or null')


def _version(value, name):
    if value is None:
        return
    if type(value) is not tuple or len(value) != 3:
        raise InputError(f'{name}: expected (major, minor, patch) tuple or null')
    for part in value:
        if type(part) is not int or part < 0:
            raise InputError(f'{name}: expected non-negative integer parts')


def _enum(value, choices, name):
    if value not in choices:
        raise InputError(f'{name}: unsupported value')


def _validate(descriptor, inventory, fit, disk, provider, request, policy):
    if not isinstance(descriptor, AcquisitionDescriptor):
        raise InputError('descriptor: expected AcquisitionDescriptor')
    if not isinstance(inventory, InventorySnapshot):
        raise InputError('inventory: expected InventorySnapshot')
    if not isinstance(fit, FitSnapshot):
        raise InputError('fit: expected FitSnapshot')
    if not isinstance(disk, DiskSnapshot):
        raise InputError('disk: expected DiskSnapshot')
    if not isinstance(provider, ProviderPreflight):
        raise InputError('provider: expected ProviderPreflight')
    if not isinstance(request, PreflightRequest):
        raise InputError('request: expected PreflightRequest')
    if not isinstance(policy, AcquisitionStoragePolicy):
        raise InputError('policy: expected AcquisitionStoragePolicy')
    _text(descriptor.catalog_id, 'catalog_id')
    for name in ('catalog_revision', 'provider', 'artifact_id', 'exact_tag', 'expected_revision'):
        _text(getattr(descriptor, name), name, True)
    _enum(descriptor.source_trust_state, TRUST_STATES, 'source_trust_state')
    _bytes(descriptor.expected_artifact_size, 'expected_artifact_size')
    if type(descriptor.provenance_summary) is not tuple or len(descriptor.provenance_summary) > 32:
        raise InputError('provenance_summary: expected bounded tuple')
    for item in descriptor.provenance_summary:
        _text(item, 'provenance_summary entry')
    _enum(inventory.state, INVENTORY_STATES, 'inventory state')
    _text(inventory.observed_revision, 'observed_revision', True)
    _enum(fit.state, FIT_STATES, 'fit state')
    if type(fit.unknown_fit_qualified) is not bool:
        raise InputError('unknown_fit_qualified: expected bool')
    _bytes(disk.filesystem_total, 'filesystem_total')
    _bytes(disk.filesystem_free, 'filesystem_free')
    for name in ('supported', 'available', 'writable', 'acquisition_in_progress', 'previous_failure'):
        _bool_or_none(getattr(provider, name), 'provider.' + name)
    _version(provider.version, 'provider.version')
    _version(provider.minimum_version, 'provider.minimum_version')
    _text(request.current_catalog_revision, 'current_catalog_revision', True)
    if type(request.allow_tight) is not bool:
        raise InputError('allow_tight: expected bool')
    for name in ('transient_min_bytes', 'safety_min_bytes'):
        value = getattr(policy, name)
        if type(value) is not int or value < 0:
            raise InputError(f'policy.{name}: expected non-negative integer')
    for name in ('transient_fraction_numerator', 'transient_fraction_denominator',
                 'safety_fraction_numerator', 'safety_fraction_denominator'):
        value = getattr(policy, name)
        if type(value) is not int or value <= 0:
            raise InputError(f'policy.{name}: expected positive integer')


def descriptor_complete(descriptor):
    """Pure completeness report; a complete descriptor still requires trust."""
    missing = [name for name in ('catalog_revision', 'provider', 'artifact_id', 'exact_tag',
                                 'expected_revision', 'expected_artifact_size')
               if getattr(descriptor, name) is None]
    if not descriptor.provenance_summary:
        missing.append('provenance_summary')
    return tuple(missing)


def disk_requirement(snapshot, expected_artifact_size, policy=AcquisitionStoragePolicy()):
    """Calculate policy storage requirements with integer byte arithmetic only."""
    if not isinstance(snapshot, DiskSnapshot) or not isinstance(policy, AcquisitionStoragePolicy):
        raise InputError('disk requirement: invalid normalized input')
    _bytes(expected_artifact_size, 'expected_artifact_size')
    _bytes(snapshot.filesystem_total, 'filesystem_total')
    _bytes(snapshot.filesystem_free, 'filesystem_free')
    if expected_artifact_size is None or snapshot.filesystem_total is None or snapshot.filesystem_free is None:
        return {'state': 'UNKNOWN', 'expected_artifact_size': expected_artifact_size,
                'transient_reserve': None, 'safety_reserve': None, 'total_required': None,
                'free_space': snapshot.filesystem_free, 'remaining_after_install': None}
    transient = max(policy.transient_min_bytes,
                    expected_artifact_size * policy.transient_fraction_numerator // policy.transient_fraction_denominator)
    safety = max(policy.safety_min_bytes,
                 snapshot.filesystem_total * policy.safety_fraction_numerator // policy.safety_fraction_denominator)
    total = expected_artifact_size + transient + safety
    remaining = snapshot.filesystem_free - total
    return {'state': 'PASS' if remaining >= 0 else 'FAIL',
            'expected_artifact_size': expected_artifact_size, 'transient_reserve': transient,
            'safety_reserve': safety, 'total_required': total,
            'free_space': snapshot.filesystem_free, 'remaining_after_install': remaining}


def catalog_revision_state(descriptor_revision, current_revision):
    if descriptor_revision is None or current_revision is None:
        return 'UNKNOWN'
    return 'CURRENT' if descriptor_revision == current_revision else 'STALE'


def descriptor_from_catalog(record, catalog_revision, source_trust_state='UNVERIFIED'):
    """Pure optional mapper for a future explicit catalog acquisition extension.

    Records without an acquisition object deliberately normalize as UNVERIFIED.
    Trust is a caller-supplied backend policy decision, never a catalog field.
    This helper never derives an install target from display/source labels or
    references.
    """
    if not isinstance(record, dict):
        raise InputError('catalog record: expected object')
    catalog_id = record.get('catalog_id')
    _text(catalog_id, 'catalog_id')
    _text(catalog_revision, 'catalog_revision', True)
    _enum(source_trust_state, TRUST_STATES, 'source_trust_state')
    acquisition = record.get('acquisition')
    if not isinstance(acquisition, dict):
        return AcquisitionDescriptor(catalog_id, catalog_revision, None, 'UNVERIFIED',
                                     None, None, None, None, ())
    provenance = acquisition.get('provenance') if isinstance(acquisition.get('provenance'), dict) else {}
    summary = tuple(value for value in (provenance.get('method'), provenance.get('observed_at'))
                    if isinstance(value, str) and value)
    return AcquisitionDescriptor(
        catalog_id=catalog_id, catalog_revision=catalog_revision,
        provider=acquisition.get('provider'), source_trust_state=source_trust_state,
        artifact_id=acquisition.get('artifact_id'), exact_tag=acquisition.get('exact_tag'),
        expected_revision=acquisition.get('expected_revision'),
        expected_artifact_size=acquisition.get('expected_size_bytes'),
        provenance_summary=summary)


def _outcome(blockers, warnings, *, no_op=False, unknown=False):
    if no_op:
        return 'NO_OP_ALREADY_INSTALLED'
    if blockers:
        return 'UNKNOWN' if unknown else 'BLOCKED'
    return 'ELIGIBLE'


def evaluate_preflight(descriptor, inventory, fit, disk, provider, request,
                       policy=AcquisitionStoragePolicy()):
    """Return deterministic acquisition eligibility from supplied snapshots only."""
    _validate(descriptor, inventory, fit, disk, provider, request, policy)
    requirement = disk_requirement(disk, descriptor.expected_artifact_size, policy)
    revision_state = catalog_revision_state(descriptor.catalog_revision, request.current_catalog_revision)
    blockers, warnings, unknown = [], [], False

    # Exact existing data needs no provider interaction, fit, trust, or storage.
    if inventory.state == 'INSTALLED_EXACT':
        return {'eligible': False, 'status': _outcome((), (), no_op=True), 'no_op': True,
                'descriptor': asdict(descriptor), 'descriptor_complete': list(descriptor_complete(descriptor)),
                'inventory_state': inventory.state, 'fit_state': fit.state, 'disk': requirement,
                'provider': asdict(provider), 'catalog_revision_state': revision_state,
                'blockers': [], 'warnings': ['ALREADY_INSTALLED_EXACT']}

    complete = descriptor_complete(descriptor)
    if complete:
        blockers.append('DESCRIPTOR_INCOMPLETE')
        unknown = True
    if descriptor.source_trust_state != 'TRUSTED':
        blockers.append('SOURCE_' + descriptor.source_trust_state)
        unknown = unknown or descriptor.source_trust_state == 'UNKNOWN'
    if revision_state == 'STALE':
        blockers.append('STALE_DESCRIPTOR')
    elif revision_state == 'UNKNOWN':
        blockers.append('CATALOG_REVISION_UNKNOWN')
        unknown = True

    if inventory.state == 'INSTALLED_DIFFERENT_REVISION':
        blockers.append('REVISION_MISMATCH')
    elif inventory.state == 'AMBIGUOUS':
        blockers.append('INVENTORY_AMBIGUOUS')
        unknown = True
    elif inventory.state == 'UNKNOWN':
        blockers.append('INVENTORY_UNKNOWN')
        unknown = True

    if provider.supported is not True:
        blockers.append('PROVIDER_UNSUPPORTED' if provider.supported is False else 'PROVIDER_SUPPORT_UNKNOWN')
        unknown = unknown or provider.supported is None
    if provider.available is not True:
        blockers.append('PROVIDER_UNAVAILABLE' if provider.available is False else 'PROVIDER_AVAILABILITY_UNKNOWN')
        unknown = unknown or provider.available is None
    if provider.writable is not True:
        blockers.append('PROVIDER_NOT_READY' if provider.writable is False else 'PROVIDER_READINESS_UNKNOWN')
        unknown = unknown or provider.writable is None
    if provider.acquisition_in_progress is True:
        blockers.append('ACQUISITION_CONFLICT')
    elif provider.acquisition_in_progress is None:
        blockers.append('ACQUISITION_STATE_UNKNOWN')
        unknown = True
    if provider.previous_failure is True:
        warnings.append('PREVIOUS_ACQUISITION_FAILED_REQUIRES_FRESH_APPROVAL')
    elif provider.previous_failure is None:
        warnings.append('PREVIOUS_ACQUISITION_STATE_UNKNOWN')
    if provider.minimum_version is not None:
        if provider.version is None:
            warnings.append('PROVIDER_VERSION_UNKNOWN')
        elif provider.version < provider.minimum_version:
            blockers.append('PROVIDER_TOO_OLD')

    if fit.state == 'TIGHT' and not request.allow_tight:
        blockers.append('TIGHT_OPT_IN_REQUIRED')
    elif fit.state == 'TIGHT':
        warnings.append('TIGHT_FIT_EXPLICITLY_ALLOWED')
    elif fit.state in ('POOR', 'UNSUPPORTED'):
        blockers.append('FIT_' + fit.state)
    elif fit.state == 'UNKNOWN' and not fit.unknown_fit_qualified:
        blockers.append('FIT_UNRESOLVED')
    elif fit.state == 'UNKNOWN':
        warnings.append('UNKNOWN_FIT_QUALIFIED_BY_SEPARATE_POLICY_EVIDENCE')

    if requirement['state'] == 'FAIL':
        blockers.append('INSUFFICIENT_STORAGE')
    elif requirement['state'] == 'UNKNOWN':
        blockers.append('STORAGE_UNKNOWN')
        unknown = True
    return {'eligible': not blockers, 'status': _outcome(blockers, warnings, unknown=unknown),
            'no_op': False, 'descriptor': asdict(descriptor),
            'descriptor_complete': list(complete), 'inventory_state': inventory.state,
            'fit_state': fit.state, 'disk': requirement, 'provider': asdict(provider),
            'catalog_revision_state': revision_state, 'blockers': sorted(set(blockers)),
            'warnings': sorted(set(warnings))}
