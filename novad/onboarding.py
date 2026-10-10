"""Pure fresh-system onboarding state classifier.

Callers provide normalized, point-in-time snapshots. This module performs no
I/O, probes, mutation, installation, activation, or model-name inference. It
only joins facts and existing policy outcomes into a conservative UI state.

Precedence is deliberate:

1. novad availability (its absence makes every backend snapshot unusable),
2. conflicting or unknown current operations,
3. current acquisition/activation operations and their failures,
4. provider availability,
5. installed inventory and explicit configuration state,
6. selected-artifact identity and current runtime readiness.

Hardware and recommendation evidence qualify candidate presentation; they do
not prove model quality or runtime readiness. Unknown hardware remains visible
but does not block a coherent, already-ready returning-user snapshot. novad now
represents a missing or invalid explicit configuration as no selection; there
is no implicit model fallback outside this contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


class InputError(ValueError):
    """A caller supplied malformed normalized input."""


NOVAD_STATES = ('AVAILABLE', 'UNAVAILABLE', 'UNKNOWN')
PROVIDER_STATES = ('AVAILABLE', 'UNAVAILABLE', 'UNSUPPORTED', 'OUTDATED', 'UNKNOWN')
HARDWARE_STATES = ('OBSERVED', 'PARTIAL', 'UNAVAILABLE', 'UNKNOWN')
CONFIG_STATES = ('VALID', 'MISSING', 'INVALID', 'MALFORMED', 'UNSUPPORTED', 'UNKNOWN')
INVENTORY_STATES = ('KNOWN', 'UNKNOWN')
ARTIFACT_STATES = (
    'NOT_INSTALLED', 'INSTALLED_EXACT', 'INSTALLED_DIFFERENT_REVISION',
    'AMBIGUOUS', 'UNKNOWN',
)
PREFLIGHT_STATES = ('ELIGIBLE', 'BLOCKED', 'NO_OP_ALREADY_INSTALLED', 'UNKNOWN')
RECOMMENDATION_STATES = (
    'RECOMMENDATION_AVAILABLE', 'SPECIALIST_ONLY',
    'NO_DEFENSIBLE_RECOMMENDATION', 'RECOMMENDATION_UNAVAILABLE', 'UNKNOWN',
)
SNAPSHOT_CONSISTENCY_STATES = ('CURRENT', 'STALE', 'UNKNOWN', 'CONFLICTING')
OPERATION_STATES = {
    'ACQUISITION': (
        'PROPOSED', 'APPROVED', 'STARTING', 'DOWNLOADING', 'VERIFYING',
        'CANCEL_REQUESTED', 'INSTALLED', 'FAILED', 'RECOVERY_REQUIRED',
        'CANCELLED', 'REJECTED', 'EXPIRED', 'UNKNOWN',
    ),
    'ACTIVATION': ('ACTIVATING', 'SUCCEEDED', 'FAILED', 'UNKNOWN'),
    'RUNTIME': ('BUSY', 'UNKNOWN'),
}


@dataclass(frozen=True)
class PrerequisiteSnapshot:
    novad: str
    provider: str
    hardware: str


@dataclass(frozen=True)
class InventorySnapshot:
    state: str
    installed_model_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionSnapshot:
    config_state: str
    selected_model_id: str | None = None
    artifact_state: str = 'UNKNOWN'
    runtime_ready: bool | None = None


@dataclass(frozen=True)
class CandidateSnapshot:
    catalog_id: str
    preflight_status: str
    artifact_state: str = 'NOT_INSTALLED'
    installed_model_id: str | None = None
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecommendationSnapshot:
    status: str = 'UNKNOWN'
    candidate_catalog_id: str | None = None


@dataclass(frozen=True)
class OperationSnapshot:
    kind: str
    state: str
    target_id: str | None = None


@dataclass(frozen=True)
class OnboardingSnapshot:
    prerequisites: PrerequisiteSnapshot
    inventory: InventorySnapshot
    selection: SelectionSnapshot
    candidates: tuple[CandidateSnapshot, ...] = ()
    recommendation: RecommendationSnapshot = RecommendationSnapshot()
    operations: tuple[OperationSnapshot, ...] = ()
    snapshot_consistency: str = 'CURRENT'


@dataclass(frozen=True)
class OnboardingResult:
    primary_state: str
    allowed_actions: tuple[str, ...]
    explanation_codes: tuple[str, ...]
    limitations: tuple[str, ...]
    candidate_catalog_ids: tuple[str, ...] = ()
    installed_model_ids: tuple[str, ...] = ()
    preferred_catalog_id: str | None = None
    target_id: str | None = None

    def as_dict(self):
        """Return a detached, JSON-compatible representation."""
        value = asdict(self)
        for name in ('allowed_actions', 'explanation_codes', 'limitations',
                     'candidate_catalog_ids', 'installed_model_ids'):
            value[name] = list(value[name])
        return value


def _enum(value, choices, name):
    if value not in choices:
        raise InputError(f'{name}: unsupported value')


def _text(value, name, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not str or not value.strip() or len(value) > 4096:
        suffix = ' or null' if nullable else ''
        raise InputError(f'{name}: expected bounded nonempty string{suffix}')


def _validate(snapshot):
    if not isinstance(snapshot, OnboardingSnapshot):
        raise InputError('snapshot: expected OnboardingSnapshot')
    if not isinstance(snapshot.prerequisites, PrerequisiteSnapshot):
        raise InputError('prerequisites: expected PrerequisiteSnapshot')
    if not isinstance(snapshot.inventory, InventorySnapshot):
        raise InputError('inventory: expected InventorySnapshot')
    if not isinstance(snapshot.selection, SelectionSnapshot):
        raise InputError('selection: expected SelectionSnapshot')
    if not isinstance(snapshot.recommendation, RecommendationSnapshot):
        raise InputError('recommendation: expected RecommendationSnapshot')
    if type(snapshot.candidates) is not tuple or type(snapshot.operations) is not tuple:
        raise InputError('candidates and operations: expected tuples')
    _enum(snapshot.snapshot_consistency, SNAPSHOT_CONSISTENCY_STATES,
          'snapshot.snapshot_consistency')

    p = snapshot.prerequisites
    _enum(p.novad, NOVAD_STATES, 'prerequisites.novad')
    _enum(p.provider, PROVIDER_STATES, 'prerequisites.provider')
    _enum(p.hardware, HARDWARE_STATES, 'prerequisites.hardware')
    _enum(snapshot.inventory.state, INVENTORY_STATES, 'inventory.state')
    if type(snapshot.inventory.installed_model_ids) is not tuple:
        raise InputError('inventory.installed_model_ids: expected tuple')
    for model_id in snapshot.inventory.installed_model_ids:
        _text(model_id, 'installed model id')
    if len(set(snapshot.inventory.installed_model_ids)) != len(snapshot.inventory.installed_model_ids):
        raise InputError('inventory.installed_model_ids: duplicate identity')

    selection = snapshot.selection
    _enum(selection.config_state, CONFIG_STATES, 'selection.config_state')
    _text(selection.selected_model_id, 'selection.selected_model_id', True)
    _enum(selection.artifact_state, ARTIFACT_STATES, 'selection.artifact_state')
    if selection.runtime_ready is not None and type(selection.runtime_ready) is not bool:
        raise InputError('selection.runtime_ready: expected bool or null')
    if selection.config_state == 'VALID' and selection.selected_model_id is None:
        raise InputError('valid configuration requires selected_model_id')

    seen_candidates = set()
    for candidate in snapshot.candidates:
        if not isinstance(candidate, CandidateSnapshot):
            raise InputError('candidate: expected CandidateSnapshot')
        _text(candidate.catalog_id, 'candidate.catalog_id')
        _enum(candidate.preflight_status, PREFLIGHT_STATES, 'candidate.preflight_status')
        _enum(candidate.artifact_state, ARTIFACT_STATES, 'candidate.artifact_state')
        _text(candidate.installed_model_id, 'candidate.installed_model_id', True)
        if type(candidate.blockers) is not tuple:
            raise InputError('candidate.blockers: expected tuple')
        for blocker in candidate.blockers:
            _text(blocker, 'candidate blocker')
        if candidate.artifact_state == 'INSTALLED_EXACT' and candidate.installed_model_id is None:
            raise InputError('exact installed candidate requires installed_model_id')
        if candidate.artifact_state != 'INSTALLED_EXACT' and candidate.installed_model_id is not None:
            raise InputError('non-exact candidate cannot carry installed_model_id')
        if candidate.catalog_id in seen_candidates:
            raise InputError('candidates: duplicate catalog_id')
        seen_candidates.add(candidate.catalog_id)

    recommendation = snapshot.recommendation
    _enum(recommendation.status, RECOMMENDATION_STATES, 'recommendation.status')
    _text(recommendation.candidate_catalog_id, 'recommendation.candidate_catalog_id', True)
    if recommendation.status == 'RECOMMENDATION_AVAILABLE' and recommendation.candidate_catalog_id is None:
        raise InputError('available recommendation requires candidate_catalog_id')

    for operation in snapshot.operations:
        if not isinstance(operation, OperationSnapshot):
            raise InputError('operation: expected OperationSnapshot')
        _enum(operation.kind, tuple(OPERATION_STATES), 'operation.kind')
        _enum(operation.state, OPERATION_STATES[operation.kind], 'operation.state')
        _text(operation.target_id, 'operation.target_id', True)


def _result(state, actions, codes, limitations=(), candidates=(), installed=(),
            preferred=None, target=None):
    return OnboardingResult(
        primary_state=state,
        allowed_actions=tuple(actions),
        explanation_codes=tuple(codes),
        limitations=tuple(limitations),
        candidate_catalog_ids=tuple(candidates),
        installed_model_ids=tuple(installed),
        preferred_catalog_id=preferred,
        target_id=target,
    )


def _hardware_limitations(state):
    if state == 'OBSERVED':
        return []
    return ['HARDWARE_PROFILE_' + state]


def _provider_operation_limitations(provider):
    return [] if provider == 'AVAILABLE' else ['PROVIDER_' + provider + '_DURING_OPERATION']


def _operation_result(operation, provider):
    limits = _provider_operation_limitations(provider)
    if operation.kind == 'ACQUISITION':
        if operation.state == 'PROPOSED':
            actions = ('DECIDE_INSTALL_APPROVAL',) if provider == 'AVAILABLE' else ()
            return _result('ACQUISITION_PROPOSED', actions,
                           ('INSTALL_APPROVAL_REQUIRED',), limits, target=operation.target_id)
        if operation.state in ('APPROVED', 'STARTING', 'DOWNLOADING', 'VERIFYING',
                               'CANCEL_REQUESTED'):
            return _result('ACQUISITION_IN_PROGRESS', (),
                           ('ACQUISITION_' + operation.state,),
                           limits + ['ACQUISITION_CANCELLATION_UNSUPPORTED'],
                           target=operation.target_id)
        if operation.state == 'FAILED':
            actions = ('RECHECK_MODEL_OPTIONS',) if provider == 'AVAILABLE' else ()
            return _result('INSTALLATION_FAILED', actions, ('INSTALLATION_FAILED',),
                           limits, target=operation.target_id)
        if operation.state == 'RECOVERY_REQUIRED':
            return _result('INSTALLATION_RECOVERY_REQUIRED', ('VIEW_RECOVERY_DETAILS',),
                           ('INSTALLATION_RECOVERY_REQUIRED',), limits,
                           target=operation.target_id)
    elif operation.kind == 'ACTIVATION':
        if operation.state == 'ACTIVATING':
            return _result('ACTIVATING', (), ('ACTIVATION_IN_PROGRESS',), limits,
                           target=operation.target_id)
        if operation.state == 'FAILED':
            actions = ('ACTIVATE_MODEL',) if provider == 'AVAILABLE' else ()
            return _result('ACTIVATION_FAILED', actions, ('ACTIVATION_FAILED',), limits,
                           target=operation.target_id)
    elif operation.kind == 'RUNTIME' and operation.state == 'BUSY':
        return _result('CHECKING', (), ('RUNTIME_OPERATION_ACTIVE',), limits,
                       target=operation.target_id)
    return None


def _candidate_state(snapshot, limitations):
    eligible = tuple(sorted(
        c.catalog_id for c in snapshot.candidates
        if c.preflight_status == 'ELIGIBLE' and c.artifact_state == 'NOT_INSTALLED'
    ))
    recommendation = snapshot.recommendation
    preferred = None
    if (recommendation.status == 'RECOMMENDATION_AVAILABLE'
            and recommendation.candidate_catalog_id in eligible):
        preferred = recommendation.candidate_catalog_id
    elif recommendation.status != 'RECOMMENDATION_AVAILABLE':
        limitations.append('QUALITY_RECOMMENDATION_' + recommendation.status)
    else:
        limitations.append('RECOMMENDATION_TARGET_NOT_INSTALL_ELIGIBLE')
    if len(eligible) > 1 and preferred is None:
        limitations.append('ELIGIBLE_CANDIDATES_UNORDERED')
    for candidate in sorted(snapshot.candidates, key=lambda value: value.catalog_id):
        if candidate.catalog_id in eligible or candidate.artifact_state == 'INSTALLED_EXACT':
            continue
        if candidate.blockers:
            limitations.extend(
                f'PREFLIGHT:{candidate.catalog_id}:{blocker}' for blocker in candidate.blockers)
        else:
            limitations.append(
                f'PREFLIGHT:{candidate.catalog_id}:{candidate.preflight_status}')
    return eligible, preferred


def classify(snapshot):
    """Classify a normalized snapshot without performing any side effect."""
    _validate(snapshot)
    p = snapshot.prerequisites

    # No backend snapshot can overrule transport-level novad absence.
    if p.novad == 'UNAVAILABLE':
        return _result('NEEDS_USER_SETUP', ('OPEN_SETUP_GUIDE', 'RETRY_CHECK'),
                       ('NOVAD_UNAVAILABLE',))
    if p.novad == 'UNKNOWN':
        return _result('CHECKING', ('RETRY_CHECK',), ('NOVAD_STATE_UNKNOWN',))

    if snapshot.snapshot_consistency != 'CURRENT':
        return _result('CHECKING', ('RETRY_CHECK',),
                       ('SNAPSHOT_' + snapshot.snapshot_consistency,),
                       _hardware_limitations(p.hardware))

    operation_results = [value for value in
                         (_operation_result(op, p.provider) for op in snapshot.operations)
                         if value is not None]
    unknown_operations = [op for op in snapshot.operations if op.state == 'UNKNOWN']
    if unknown_operations or len(operation_results) > 1:
        codes = ('ACTIVE_OPERATION_UNKNOWN',) if unknown_operations else ('CONFLICTING_ACTIVE_OPERATIONS',)
        return _result('CHECKING', ('RETRY_CHECK',), codes,
                       _hardware_limitations(p.hardware))
    if operation_results:
        value = operation_results[0]
        return OnboardingResult(
            value.primary_state, value.allowed_actions, value.explanation_codes,
            tuple((*value.limitations, *_hardware_limitations(p.hardware))),
            value.candidate_catalog_ids, value.installed_model_ids,
            value.preferred_catalog_id, value.target_id,
        )

    operation_codes = []
    operation_limits = []
    operation_target = None
    for operation in snapshot.operations:
        operation_target = operation.target_id or operation_target
        if operation.kind == 'ACQUISITION':
            operation_codes.append('ACQUISITION_' + operation.state)
        elif operation.state == 'SUCCEEDED':
            operation_limits.append('ACTIVATION_SUCCESS_REQUIRES_FRESH_READINESS')

    if p.provider in ('UNAVAILABLE', 'UNSUPPORTED', 'OUTDATED'):
        return _result('NEEDS_USER_SETUP', ('OPEN_PROVIDER_SETUP', 'RETRY_CHECK'),
                       ('PROVIDER_' + p.provider,),
                       (*operation_limits, *_hardware_limitations(p.hardware)))
    if p.provider == 'UNKNOWN':
        return _result('CHECKING', ('RETRY_CHECK',), ('PROVIDER_STATE_UNKNOWN',),
                       (*operation_limits, *_hardware_limitations(p.hardware)))

    inventory = snapshot.inventory
    selection = snapshot.selection
    coherent_returning_ready = (
        inventory.state == 'KNOWN'
        and selection.config_state == 'VALID'
        and selection.artifact_state == 'INSTALLED_EXACT'
        and selection.selected_model_id in inventory.installed_model_ids
        and selection.runtime_ready is True
    )

    if p.hardware == 'UNAVAILABLE':
        return _result('NEEDS_USER_SETUP', ('OPEN_SETUP_GUIDE', 'RETRY_CHECK'),
                       (*operation_codes, 'HARDWARE_PROFILE_UNAVAILABLE'), operation_limits)
    if p.hardware == 'UNKNOWN' and not coherent_returning_ready:
        return _result('CHECKING', ('RETRY_CHECK',),
                       (*operation_codes, 'HARDWARE_PROFILE_UNKNOWN'), operation_limits)

    limitations = [*operation_limits, *_hardware_limitations(p.hardware)]
    if inventory.state == 'UNKNOWN':
        return _result('CHECKING', ('RETRY_CHECK',),
                       (*operation_codes, 'INSTALLED_INVENTORY_UNKNOWN'), limitations)

    # runtime_ready=true is meaningful only for an explicit, exact installed selection.
    if selection.runtime_ready is True and not (
        selection.config_state == 'VALID'
        and selection.artifact_state == 'INSTALLED_EXACT'
        and selection.selected_model_id in inventory.installed_model_ids
    ):
        return _result('CHECKING', ('RETRY_CHECK',), ('CONFLICTING_READINESS_SNAPSHOT',),
                       limitations)

    installed = tuple(sorted(inventory.installed_model_ids))

    mismatched_candidates = tuple(sorted(
        c.catalog_id for c in snapshot.candidates
        if c.artifact_state == 'INSTALLED_EXACT'
        and c.installed_model_id not in installed
    ))
    if mismatched_candidates:
        return _result('CHECKING', ('RETRY_CHECK',),
                       ('CONFLICTING_CATALOG_INVENTORY',), limitations,
                       candidates=mismatched_candidates, installed=installed)

    if not installed:
        eligible, preferred = _candidate_state(snapshot, limitations)
        codes = [*operation_codes, 'NO_INSTALLED_MODELS']
        if eligible:
            codes.append('ELIGIBLE_INSTALL_CANDIDATE' if len(eligible) == 1
                         else 'MULTIPLE_ELIGIBLE_INSTALL_CANDIDATES')
            action = 'PROPOSE_INSTALL' if len(eligible) == 1 or preferred else 'CHOOSE_INSTALL_CANDIDATE'
            return _result('NEEDS_MODEL', (action,), codes, limitations,
                           candidates=eligible, preferred=preferred,
                           target=preferred or (eligible[0] if len(eligible) == 1 else None))
        codes.append('NO_ELIGIBLE_CATALOG_CANDIDATE')
        return _result('NEEDS_MODEL', ('RETRY_CHECK',), codes, limitations)

    if selection.config_state == 'UNKNOWN':
        return _result('CHECKING', ('RETRY_CHECK',),
                       (*operation_codes, 'MODEL_CONFIGURATION_UNKNOWN'), limitations)
    if selection.config_state in ('MISSING', 'INVALID', 'MALFORMED', 'UNSUPPORTED'):
        code = 'MODEL_CONFIGURATION_' + selection.config_state
        limitations.append('SERVER_DEFAULT_FALLBACK_IS_NOT_EXPLICIT_SELECTION')
        verified_target = next((
            c.installed_model_id for c in snapshot.candidates
            if operation_target is not None
            and c.catalog_id == operation_target
            and c.artifact_state == 'INSTALLED_EXACT'
            and c.installed_model_id in installed
        ), None)
        actions = ('ACTIVATE_MODEL',) if verified_target else ('CHOOSE_INSTALLED_MODEL',)
        return _result('NEEDS_ACTIVATION', actions, (*operation_codes, code),
                       limitations, installed=installed,
                       target=verified_target or operation_target)

    # An explicit configured selection can only become ready with matching inventory.
    if selection.artifact_state != 'INSTALLED_EXACT':
        code = {
            'NOT_INSTALLED': 'SELECTED_MODEL_NOT_INSTALLED',
            'INSTALLED_DIFFERENT_REVISION': 'SELECTED_MODEL_REVISION_MISMATCH',
            'AMBIGUOUS': 'SELECTED_MODEL_INVENTORY_AMBIGUOUS',
            'UNKNOWN': 'SELECTED_MODEL_INVENTORY_UNKNOWN',
        }[selection.artifact_state]
        if selection.artifact_state in ('AMBIGUOUS', 'UNKNOWN'):
            return _result('CHECKING', ('RETRY_CHECK',), (*operation_codes, code),
                           limitations, installed=installed, target=selection.selected_model_id)
        eligible, preferred = _candidate_state(snapshot, limitations)
        actions = ['CHOOSE_INSTALLED_MODEL']
        if eligible:
            actions.append('PROPOSE_INSTALL' if len(eligible) == 1 or preferred
                           else 'CHOOSE_INSTALL_CANDIDATE')
        return _result('NEEDS_ACTIVATION', actions, (*operation_codes, code),
                       limitations, candidates=eligible, installed=installed,
                       preferred=preferred, target=selection.selected_model_id)

    if selection.selected_model_id not in installed:
        return _result('CHECKING', ('RETRY_CHECK',),
                       (*operation_codes, 'CONFLICTING_SELECTED_INVENTORY'), limitations,
                       installed=installed, target=selection.selected_model_id)
    if selection.runtime_ready is None:
        return _result('CHECKING', ('RETRY_CHECK',),
                       (*operation_codes, 'RUNTIME_READINESS_UNKNOWN'), limitations,
                       installed=installed, target=selection.selected_model_id)
    if selection.runtime_ready is False:
        # Konstantin, 2026-10-09: the 10-minute auto-unload must not block chat. The model
        # was explicitly selected and is installed; the next chat request loads it (the
        # runtime reports loading_model meanwhile). LOAD AND VERIFY stays available.
        return _result('READY', ('SEND_FIRST_MESSAGE', 'LOAD_AND_VERIFY'),
                       (*operation_codes, 'SELECTED_MODEL_NOT_LOADED'),
                       (*limitations, 'LOADS_ON_FIRST_MESSAGE'),
                       installed=installed, target=selection.selected_model_id)
    return _result('READY', ('SEND_FIRST_MESSAGE',),
                   (*operation_codes, 'SELECTED_MODEL_READY'), limitations,
                   installed=installed, target=selection.selected_model_id)
