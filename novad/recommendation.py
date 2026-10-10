"""Pure recommendation policy v1. No adapters, I/O, clock or runtime imports.

evaluate(candidates, request, comparisons=(), policy=Policy()) returns a detached
JSON-compatible report. Inputs are frozen normalized dataclasses, not raw API
payloads. Invalid types/enums raise InputError; absent evidence causes abstention.
See RECOMMENDATION_POLICY.md and RECOMMENDATION_INPUTS.md for the contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from types import UnionType
from typing import get_args, get_origin, get_type_hints

PROFILES = ('GENERAL', 'CODING', 'DEEP_REASONING', 'FAST_BACKGROUND', 'VISION')
CONFIDENCES = ('INSUFFICIENT', 'LOW', 'MEDIUM', 'HIGH')
CAPABILITIES = ('chat', 'tools', 'thinking', 'coding', 'vision', 'instruction_following')
SOURCES = ('LOCAL_FACT', 'UPSTREAM_METADATA', 'CURATED',
           'REPRODUCIBLE_BENCHMARK', 'LOCAL_CALIBRATION')
DIMENSIONS = ('TASK', 'GENERAL_QUALITY', 'PERFORMANCE', 'SPECIALIST')


class InputError(ValueError):
    """Caller supplied an invalid normalized input, not an evidence shortfall."""


@dataclass(frozen=True)
class Artifact:
    provider: str
    model_id: str
    revision: str | None = None


@dataclass(frozen=True)
class Evidence:
    reference: str
    method: str
    source: str
    claim_kind: str
    artifacts: tuple[Artifact, ...]
    profile: str
    workload: str
    confidence: str = 'INSUFFICIENT'
    applicable: bool | None = None
    observed_at: str | None = None
    corroborated: bool = False
    supporting_references: tuple[str, ...] = ()
    environment: str | None = None


@dataclass(frozen=True)
class TaskEvidence:
    evidence: Evidence
    suitable: bool | None = None
    supported_capabilities: tuple[str, ...] = ()
    reliable_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capabilities:
    chat: bool | None = None
    tools: bool | None = None
    thinking: bool | None = None
    coding: bool | None = None
    vision: bool | None = None
    instruction_following: bool | None = None


@dataclass(frozen=True)
class Fit:
    rating: str = 'UNKNOWN'
    confidence: str = 'INSUFFICIENT'
    supported: bool | None = None
    revision: str | None = None
    workload: str | None = None
    environment: str | None = None
    native_context: int | None = None
    sustainable_context: int | None = None
    headroom_bytes: int | None = None
    conditional_offload: bool = True
    observed_at: str | None = None
    policy_revision: str | None = None
    modality: str = 'text'


@dataclass(frozen=True)
class Conditions:
    """Exact tested configuration; signatures are opaque caller-normalized IDs."""
    hardware: str
    backend: str
    backend_version: str
    device: str
    offload: str
    cache_precision: str
    runtime_options: str
    modality: str
    concurrency: int


@dataclass(frozen=True)
class Budget:
    policy_revision: str
    operating_bytes: int
    system_reserve_bytes: int
    operating_margin_bytes: int


@dataclass(frozen=True)
class Trial:
    reference: str
    observed_at: str
    prompt_tokens: int
    output_tokens: int
    peak_runtime_bytes: int
    minimum_system_free_bytes: int
    completed: bool
    oom: bool
    allocation_failure: bool
    undeclared_offload: bool
    contention_failure: bool


@dataclass(frozen=True)
class Calibration:
    evidence: Evidence
    protocol: str
    protocol_version: str
    acceptance_criteria: str
    measurement_source: str
    minimum_trials: int
    conditions: Conditions
    budget: Budget
    configured_context: int
    usable_context: int
    prompt_allowance: int
    output_allowance: int
    trials: tuple[Trial, ...]
    all_trials_recorded: bool
    conflicts_with_fit: bool = False


@dataclass(frozen=True)
class Acquisition:
    status: str = 'UNKNOWN'
    download_bytes: int | None = None
    incremental_bytes: int | None = None
    staging_bytes: int | None = None
    reserve_bytes: int | None = None
    available_bytes: int | None = None
    observed_at: str | None = None
    accounting_reference: str | None = None


@dataclass(frozen=True)
class Candidate:
    catalog_id: str
    artifact: Artifact
    fit: Fit = Fit()
    capabilities: Capabilities = Capabilities()
    installed: bool | None = None
    provider_available: bool | None = None
    identity_verified: bool | None = None
    storage_bytes: int | None = None
    acquisition: Acquisition = Acquisition()
    tasks: tuple[TaskEvidence, ...] = ()
    calibration: Calibration | None = None
    catalog_revision: str | None = None
    catalog_checked_at: str | None = None


@dataclass(frozen=True)
class Comparison:
    left: str
    right: str
    dimension: str
    outcome: str  # LEFT, RIGHT, EQUIVALENT; absence means INCOMPARABLE
    evidence: Evidence
    explanation: str
    specialist_scope: str | None = None


@dataclass(frozen=True)
class Policy:
    version: str = '1'
    # Pinned v1 defaults; adapter may inject the active HeadroomPolicy defaults.
    minimum_context: int = 4096
    target_context: int = 32768


@dataclass(frozen=True)
class Request:
    workload: str
    environment: str
    profile: str = 'GENERAL'
    scope: str = 'installed'
    minimum_context: int | None = None
    target_context: int | None = None
    required_capabilities: tuple[str, ...] = ()
    allow_tight: bool = False
    conditions: Conditions | None = None
    budget: Budget | None = None
    specialist_scope: str | None = None


def _check(value, annotation, path):
    """Strict dataclass/tuple/primitives only. In particular bool is not int."""
    if get_origin(annotation) is UnionType:
        if not any(_matches(value, part) for part in get_args(annotation)):
            raise InputError(f'{path}: invalid type')
        return
    if get_origin(annotation) is tuple:
        if type(value) is not tuple or len(value) > 256:
            raise InputError(f'{path}: expected tuple with at most 256 items')
        for item in value:
            _check(item, get_args(annotation)[0], path)
        return
    if type(value) is not annotation:
        raise InputError(f'{path}: invalid type')
    if is_dataclass(value):
        hints = get_type_hints(type(value))
        for field in fields(value):
            _check(getattr(value, field.name), hints[field.name], f'{path}.{field.name}')
            if field.name in ('observed_at', 'catalog_checked_at'):
                stamp = getattr(value, field.name)
                if stamp is not None:
                    try:
                        parsed = datetime.fromisoformat(stamp)
                        if parsed.tzinfo is None:
                            raise ValueError('missing timezone')
                    except ValueError as exc:
                        raise InputError(f'{path}.{field.name}: expected timezone-aware ISO timestamp') from exc
    elif isinstance(value, str) and (not value.strip() or len(value) > 4096):
        raise InputError(f'{path}: expected nonempty bounded string')
    elif type(value) is int and value < 0:
        raise InputError(f'{path}: negative value')


def _matches(value, annotation):
    try:
        _check(value, annotation, 'value')
        return True
    except InputError:
        return False


def _enum(value, choices, name):
    if value not in choices:
        raise InputError(f'{name}: unsupported value')


def _evidence_shape(e):
    _enum(e.source, SOURCES, 'evidence source')
    _enum(e.claim_kind, ('FACT', 'QUALITY_JUDGMENT'), 'claim kind')
    _enum(e.confidence, CONFIDENCES, 'confidence')
    _enum(e.profile, PROFILES, 'evidence profile')


def _validate(candidates, request, comparisons, policy):
    _check(candidates, tuple[Candidate, ...], 'candidates')
    _check(request, Request, 'request')
    _check(comparisons, tuple[Comparison, ...], 'comparisons')
    _check(policy, Policy, 'policy')
    _enum(policy.version, ('1',), 'policy version')
    _enum(request.profile, PROFILES, 'profile')
    _enum(request.scope, ('installed', 'known'), 'scope')
    for cap in request.required_capabilities:
        _enum(cap, CAPABILITIES, 'required capability')
    minimum = request.minimum_context if request.minimum_context is not None else policy.minimum_context
    target = request.target_context if request.target_context is not None else policy.target_context
    if not 0 < policy.minimum_context <= policy.target_context or not 0 < minimum <= target:
        raise InputError('context: require 0 < minimum <= target')
    ids = [c.catalog_id for c in candidates]
    identities = [(c.artifact.provider, c.artifact.model_id, c.artifact.revision) for c in candidates]
    if len(set(ids)) != len(ids) or len(set(identities)) != len(identities):
        raise InputError('duplicate candidate identity')
    for c in candidates:
        _enum(c.fit.rating, ('EXCELLENT', 'GOOD', 'TIGHT', 'POOR', 'UNSUPPORTED', 'UNKNOWN'), 'fit')
        _enum(c.fit.confidence, CONFIDENCES, 'fit confidence')
        _enum(c.acquisition.status, ('FEASIBLE', 'BLOCKED', 'UNKNOWN'), 'acquisition')
        for task in c.tasks:
            _evidence_shape(task.evidence)
            for cap in task.supported_capabilities + task.reliable_capabilities:
                _enum(cap, CAPABILITIES, 'task capability')
        if c.calibration:
            _evidence_shape(c.calibration.evidence)
    for comp in comparisons:
        _evidence_shape(comp.evidence)
        _enum(comp.dimension, DIMENSIONS, 'dimension')
        _enum(comp.outcome, ('LEFT', 'RIGHT', 'EQUIVALENT'), 'comparison outcome')
        if comp.left not in ids or comp.right not in ids or comp.left == comp.right:
            raise InputError('comparison: unknown or identical candidate IDs')
    return minimum, target


def _weakest(*levels):
    return min(levels, key=CONFIDENCES.index)


def _proof(e, artifacts, request, quality=False, performance=False):
    if (e.applicable is not True or e.profile != request.profile or
            e.workload != request.workload or
            any(a.revision is None or a not in e.artifacts for a in artifacts)):
        return 'INSUFFICIENT'
    empirical = e.source in ('REPRODUCIBLE_BENCHMARK', 'LOCAL_CALIBRATION')
    if quality and (e.claim_kind != 'QUALITY_JUDGMENT' or
                    not (empirical or (e.source == 'CURATED' and e.supporting_references))):
        return 'INSUFFICIENT'
    if performance and (not empirical or e.claim_kind != 'FACT' or
                        e.environment != request.environment):
        return 'INSUFFICIENT'
    confidence = e.confidence
    if not e.observed_at:
        confidence = _weakest(confidence, 'LOW')
    if not e.corroborated:
        confidence = _weakest(confidence, 'MEDIUM')
    return confidence


def _calibrated(c, request):
    cal = c.calibration
    if cal is None or request.conditions is None or request.budget is None:
        return False
    b = cal.budget
    if (cal.evidence.source != 'LOCAL_CALIBRATION' or cal.evidence.claim_kind != 'FACT' or
            _proof(cal.evidence, (c.artifact,), request, performance=True) == 'INSUFFICIENT' or
            not cal.evidence.observed_at or cal.conditions != request.conditions or
            b != request.budget or cal.conflicts_with_fit or not cal.all_trials_recorded or
            c.fit.policy_revision != b.policy_revision or
            cal.conditions.concurrency < 1 or cal.minimum_trials < 2 or
            len(cal.trials) < cal.minimum_trials or
            len({t.reference for t in cal.trials}) != len(cal.trials) or
            not 0 < cal.usable_context <= cal.configured_context or
            cal.prompt_allowance <= 0 or cal.output_allowance <= 0 or
            cal.prompt_allowance + cal.output_allowance < cal.usable_context or
            cal.prompt_allowance + cal.output_allowance > cal.configured_context or
            b.operating_bytes <= 0 or b.system_reserve_bytes <= 0 or
            not 0 < b.operating_margin_bytes < b.operating_bytes or
            (request.profile == 'VISION' and cal.conditions.modality != 'vision')):
        return False
    for t in cal.trials:
        if (not t.completed or t.oom or t.allocation_failure or t.undeclared_offload or
                t.contention_failure or t.prompt_tokens < cal.prompt_allowance or
                t.output_tokens < cal.output_allowance or
                t.prompt_tokens + t.output_tokens > cal.configured_context or
                t.peak_runtime_bytes <= 0 or
                t.peak_runtime_bytes + b.operating_margin_bytes > b.operating_bytes or
                t.minimum_system_free_bytes < b.system_reserve_bytes):
            return False
    return True


def _acquisition(c):
    a = c.acquisition
    if a.status == 'BLOCKED':
        return 'BLOCKED'
    values = (c.storage_bytes, a.download_bytes, a.incremental_bytes,
              a.staging_bytes, a.reserve_bytes, a.available_bytes)
    if None in values or not a.observed_at or not a.accounting_reference:
        return 'UNKNOWN'
    if a.incremental_bytes + a.staging_bytes + a.reserve_bytes > a.available_bytes:
        return 'BLOCKED'
    return a.status


def _candidate(c, request, minimum, target):
    reasons, warnings, references = [], [], []
    status = 'ELIGIBLE'
    confidence = c.fit.confidence

    def gate(code, unresolved=False):
        nonlocal status
        reasons.append(code)
        if not unresolved or status != 'EXCLUDED':
            status = 'UNRESOLVED' if unresolved else 'EXCLUDED'

    if c.identity_verified is not True or c.artifact.revision is None:
        gate('IDENTITY_UNRESOLVED', True)
    if request.scope == 'installed':
        if c.installed is not True:
            gate('NOT_INSTALLED' if c.installed is False else 'INSTALLED_UNKNOWN', c.installed is None)
        if c.provider_available is not True:
            gate('PROVIDER_UNAVAILABLE', True)
    elif c.provider_available is not True:
        warnings.append('Provider availability must be established before use.')
        if status == 'ELIGIBLE':
            status = 'CONDITIONAL'
        confidence = _weakest(confidence, 'LOW')
    if request.scope == 'known' and c.installed is None:
        gate('INSTALLED_UNKNOWN', True)
    acquisition = _acquisition(c) if c.installed is False else None
    if request.scope == 'known' and c.installed is False and acquisition != 'FEASIBLE':
        gate('STORAGE_BLOCKED' if acquisition == 'BLOCKED' else 'STORAGE_UNKNOWN', acquisition != 'BLOCKED')
    if c.fit.supported is not True:
        gate('BACKEND_UNSUPPORTED' if c.fit.supported is False else 'BACKEND_UNKNOWN', c.fit.supported is None)
    if c.fit.rating in ('POOR', 'UNSUPPORTED'):
        gate('FIT_' + c.fit.rating)
    if c.fit.revision != c.artifact.revision or c.fit.revision is None:
        gate('REVISION_EVIDENCE_MISMATCH', True)
    if c.fit.workload != request.workload or c.fit.environment != request.environment:
        gate('FIT_SCOPE_UNRESOLVED', True)
    if request.profile == 'VISION' and c.fit.modality != 'vision' and c.fit.rating != 'UNKNOWN':
        gate('VISION_SUSTAINABILITY_UNRESOLVED', True)

    calibrated = _calibrated(c, request)
    context = c.fit.sustainable_context
    context_source = 'STATIC_ESTIMATE'
    if c.calibration and c.calibration.conflicts_with_fit:
        gate('CONFLICTING_FIT_EVIDENCE', True)
    if c.fit.rating == 'UNKNOWN':
        if calibrated:
            context = c.calibration.usable_context
            context_source = 'OBSERVED_QUALIFIED'
            confidence = _weakest('MEDIUM', _proof(c.calibration.evidence, (c.artifact,), request))
            references.append(c.calibration.evidence.reference)
            warnings.append('Static fit remains UNKNOWN; usability is bounded by supplied calibration.')
        else:
            context = None
            context_source = 'UNKNOWN'
            gate('FIT_UNRESOLVED', True)
    if c.fit.rating == 'TIGHT':
        warnings.append('TIGHT fit: reduced operating margin and/or usable context.')
        if not request.allow_tight:
            gate('TIGHT_OPT_IN_REQUIRED')
        elif status == 'ELIGIBLE':
            status = 'CONDITIONAL'
    if context is None:
        gate('CONTEXT_UNRESOLVED', True)
    elif context < minimum or (c.fit.native_context is not None and c.fit.native_context < minimum):
        gate('BELOW_MINIMUM_CONTEXT')
    elif context < target:
        warnings.append('Usable context meets the minimum but is below the requested target.')
    if context is not None and c.fit.native_context is not None and context > c.fit.native_context:
        gate('CONFLICTING_CONTEXT_EVIDENCE', True)
    if c.fit.conditional_offload:
        confidence = _weakest(confidence, 'MEDIUM')
        warnings.append('Full offload/backend assumptions remain conditional.')
    if request.scope == 'known' and c.provider_available is not True:
        confidence = _weakest(confidence, 'LOW')

    tasks = [t for t in c.tasks if _proof(t.evidence, (c.artifact,), request, quality=True) != 'INSUFFICIENT']
    positive = [t for t in tasks if t.suitable is True]
    if any(t.suitable is False for t in tasks):
        gate('TASK_EVIDENCE_CONFLICT' if positive else 'TASK_UNSUITABLE', bool(positive))
    required = set(request.required_capabilities) | {'chat'}
    if request.profile == 'VISION':
        required.add('vision')
    for cap in sorted(required):
        declared = getattr(c.capabilities, cap)
        proven = any(cap in t.supported_capabilities for t in positive)
        if declared is False:
            gate('REQUIRED_CAPABILITY_FALSE:' + cap)
        elif declared is None and not proven:
            gate('REQUIRED_CAPABILITY_UNKNOWN:' + cap, True)
    if 'tools' in required and not any('tools' in t.reliable_capabilities for t in positive):
        reasons.append('TOOL_RELIABILITY_UNKNOWN')
        positive = []
    if not positive:
        reasons.append('TASK_EVIDENCE_INSUFFICIENT')
        confidence = 'INSUFFICIENT'
    else:
        confidence = _weakest(confidence, *(_proof(t.evidence, (c.artifact,), request, quality=True) for t in positive))
        references.extend(t.evidence.reference for t in positive)
    if status in ('EXCLUDED', 'UNRESOLVED'):
        confidence = 'INSUFFICIENT'
    selectable = confidence != 'INSUFFICIENT' and status in ('ELIGIBLE', 'CONDITIONAL')
    if not selectable and not reasons:
        reasons.append('FIT_CONFIDENCE_INSUFFICIENT')
    return {'catalog_id': c.catalog_id, 'eligibility': status, 'selectable': selectable,
            'reasons': sorted(set(reasons)), 'confidence': confidence,
            'fit_state': c.fit.rating, 'usable_context': context,
            'context_source': context_source, 'context_band':
            ('UNRESOLVED' if context is None else 'BELOW_MINIMUM' if context < minimum
             else 'TARGET_MET' if context >= target else 'MINIMUM_ONLY'),
            'why': [f'{c.fit.rating} static fit', f'{context_source} usable context: {context}',
                    'Task suitability supported by supplied evidence'] if selectable else [],
            'trade_offs': warnings, 'evidence_references': sorted(set(references)),
            'installed': c.installed, 'requires_installation':
            (None if c.installed is None else not c.installed),
            'acquisition_feasibility': acquisition, 'inputs': asdict(c)}


def _dimension(a, b, dimension, comparisons, request):
    matches = []
    for comp in comparisons:
        if comp.dimension != dimension or {comp.left, comp.right} != {a.catalog_id, b.catalog_id}:
            continue
        if dimension == 'SPECIALIST' and (not request.specialist_scope or comp.specialist_scope != request.specialist_scope):
            continue
        confidence = _proof(comp.evidence, (a.artifact, b.artifact), request,
                            quality=dimension != 'PERFORMANCE', performance=dimension == 'PERFORMANCE')
        if confidence == 'INSUFFICIENT':
            continue
        winner = comp.left if comp.outcome == 'LEFT' else comp.right
        value = 0 if comp.outcome == 'EQUIVALENT' else (-1 if winner == a.catalog_id else 1)
        matches.append((value, confidence, comp.evidence.reference, comp.explanation))
    if not matches or len({m[0] for m in matches}) != 1:
        return None, 'INSUFFICIENT', [], ['COMPARISON_INCOMPARABLE:' + dimension]
    return matches[0][0], _weakest(*(m[1] for m in matches)), sorted({m[2] for m in matches}), sorted({m[3] for m in matches})


def _compare(a, b, rows, comparisons, request):
    for key in ('sustainability', 'context'):
        av = a.fit.rating == 'TIGHT' if key == 'sustainability' else rows[a.catalog_id]['context_band'] != 'TARGET_MET'
        bv = b.fit.rating == 'TIGHT' if key == 'sustainability' else rows[b.catalog_id]['context_band'] != 'TARGET_MET'
        if av != bv:
            return (-1 if not av else 1), 'HIGH', [], [key + ' band takes priority']
    dimensions = ('TASK', 'PERFORMANCE', 'GENERAL_QUALITY') if request.profile == 'FAST_BACKGROUND' else ('TASK', 'GENERAL_QUALITY', 'PERFORMANCE')
    confidence, refs, why = 'HIGH', [], []
    for dimension in dimensions:
        result, level, evidence, explanations = _dimension(a, b, dimension, comparisons, request)
        confidence = _weakest(confidence, level)
        refs += evidence
        why += explanations
        if result != 0:
            return result, confidence, refs, why
    if a.storage_bytes is None or b.storage_bytes is None:
        return None, 'INSUFFICIENT', refs, why + ['STORAGE_COMPARISON_UNKNOWN']
    result = (a.storage_bytes > b.storage_bytes) - (a.storage_bytes < b.storage_bytes)
    return result, confidence, refs, why + ['Storage is the final tie-breaker; it is not a quality judgment.']


def _quality(a, b, comparisons, request):
    task = _dimension(a, b, 'TASK', comparisons, request)
    if task[0] != 0:
        return task
    general = _dimension(a, b, 'GENERAL_QUALITY', comparisons, request)
    return (general[0], _weakest(task[1], general[1]),
            task[2] + general[2], task[3] + general[3])


def evaluate(candidates: tuple[Candidate, ...], request: Request,
             comparisons: tuple[Comparison, ...] = (), policy: Policy = Policy()):
    """Evaluate immutable snapshots; no global state, discovery or side effects."""
    minimum, target = _validate(candidates, request, comparisons, policy)
    ordered = sorted(candidates, key=lambda c: c.catalog_id)
    rows = {c.catalog_id: _candidate(c, request, minimum, target) for c in ordered}
    eligible = [c for c in ordered if rows[c.catalog_id]['selectable']]
    tiers = {name: {'candidates': [], 'abstention_reason': 'NO_SUPPORTED_CANDIDATE'}
             for name in ('recommended', 'faster', 'max_quality', 'specialist')}

    def selection(c, comparisons_used):
        row = dict(rows[c.catalog_id])
        row['confidence'] = _weakest(row['confidence'], *(p[1] for p in comparisons_used))
        row['why'] = row['why'] + sorted({why for p in comparisons_used for why in p[3]})
        row['evidence_references'] = sorted(set(row['evidence_references']) | {ref for p in comparisons_used for ref in p[2]})
        row['trade_offs'] = row['trade_offs'] + [
            'Larger storage footprint than ' + other.catalog_id + '.'
            for other in eligible if c.storage_bytes is not None and
            other.storage_bytes is not None and c.storage_bytes > other.storage_bytes]
        return row

    winners = []
    for c in eligible:
        pairs = [_compare(c, other, rows, comparisons, request) for other in eligible if other != c]
        if all(p[0] in (-1, 0) for p in pairs):
            winners.append(c)
            tiers['recommended']['candidates'].append(selection(c, pairs))
    if eligible and not winners:
        tiers['recommended']['abstention_reason'] = 'COMPARISON_INCOMPARABLE_OR_CONFLICTING'

    # Alternatives need a defensible baseline. One candidate occupies one tier.
    used = {c.catalog_id for c in winners}
    for name, dimension in (('max_quality', 'TASK'), ('faster', 'PERFORMANCE'), ('specialist', 'SPECIALIST')):
        baselines = winners if name != 'specialist' or winners else eligible
        if not baselines or (name == 'specialist' and not request.specialist_scope):
            continue
        alternatives = []
        for c in eligible:
            if c.catalog_id in used:
                continue
            def compare_alternative(left, right):
                return (_quality(left, right, comparisons, request) if name == 'max_quality'
                        else _dimension(left, right, dimension, comparisons, request))
            pairs = [compare_alternative(c, w) for w in baselines if w != c]
            if pairs and all(p[0] == -1 for p in pairs):
                alternatives.append((c, pairs))
        # Do not imply alternatives are coequal unless they can be compared.
        for c, pairs in alternatives:
            peers = [compare_alternative(c, other)
                     for other, _ in alternatives if other != c]
            if all(p[0] in (-1, 0) for p in peers):
                tiers[name]['candidates'].append(selection(c, pairs + peers))
                used.add(c.catalog_id)
    for tier in tiers.values():
        if tier['candidates']:
            tier['abstention_reason'] = None
    recommended = tiers['recommended']['candidates']
    selected = [row for tier in tiers.values() for row in tier['candidates']]
    return {'schema_version': 1, 'policy_version': policy.version,
            'profile': request.profile, 'scope': request.scope,
            'request': asdict(request), 'minimum_context': minimum, 'target_context': target,
            'status': ('RECOMMENDATION_AVAILABLE' if recommended else
                       'SPECIALIST_ONLY' if selected else 'NO_DEFENSIBLE_RECOMMENDATION'),
            'confidence': _weakest(*(r['confidence'] for r in (recommended or selected))) if selected else 'INSUFFICIENT',
            'tiers': tiers, 'candidates': list(rows.values()),
            'excluded': [r for r in rows.values() if not r['selectable']],
            'limitations': ['Evidence and applicability are supplied by callers; no discovery or calibration runs.',
                            'Recommendations are bounded by the supplied policy, workload and evidence.',
                            'Unknown comparisons cause abstention; no numeric quality scores are inferred.']}
