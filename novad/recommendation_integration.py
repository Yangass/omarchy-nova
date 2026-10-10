"""Read-only live-snapshot adapter for the pure recommendation evaluator.

This module normalizes already-collected catalog and fit snapshots. It performs
no discovery, network I/O, calibration, model operation or state mutation. The
explicit ``load_and_build_recommendations`` boundary additionally reads the
optional local evidence seed; ``build_recommendations`` itself remains pure.
"""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict, replace
from urllib.parse import parse_qsl

from acquisition import DiskSnapshot, disk_requirement
from evidence import EvidenceStore, confidence_ceiling, load_evidence
from model_fit import POLICY as FIT_POLICY
from recommendation import (Acquisition, Artifact, Capabilities, Candidate, Comparison,
                            Evidence, Fit, InputError, Policy, PROFILES, Request, TaskEvidence,
                            evaluate)

WORKLOAD = 'live_local_text_single_sequence'
ENVIRONMENT = 'live_hardware_fit_snapshot'
MAX_CONTEXT = 1_048_576


class RecommendationRequestError(ValueError):
    """The recommendation endpoint request is malformed or unsupported."""


def parse_request(query: str) -> Request:
    """Parse only the documented query keys into a canonical evaluator request."""
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError as error:
        raise RecommendationRequestError('invalid recommendation query') from error
    values = {}
    for key, value in pairs:
        if key not in ('profile', 'scope', 'allow_tight', 'required_context') or key in values:
            raise RecommendationRequestError('unsupported or repeated recommendation query parameter')
        values[key] = value
    profile = values.get('profile', 'GENERAL').upper()
    scope = values.get('scope', 'installed').lower()
    if profile not in PROFILES:
        raise RecommendationRequestError('unsupported task profile')
    if scope not in ('installed', 'known'):
        raise RecommendationRequestError('unsupported recommendation scope')
    raw_tight = values.get('allow_tight', 'false').lower()
    if raw_tight not in ('true', 'false'):
        raise RecommendationRequestError('allow_tight must be true or false')
    context = None
    if 'required_context' in values:
        raw_context = values['required_context']
        if not raw_context.isascii() or not raw_context.isdecimal():
            raise RecommendationRequestError('required_context must be a positive integer')
        context = int(raw_context)
        if not 0 < context <= MAX_CONTEXT:
            raise RecommendationRequestError('required_context is outside the supported range')
    try:
        # A required context is a hard operating requirement, so use it for both
        # the policy minimum and target rather than treating it as a soft hint.
        return Request(workload=WORKLOAD, environment=ENVIRONMENT, profile=profile,
                       scope=scope, minimum_context=context, target_context=context,
                       allow_tight=raw_tight == 'true')
    except InputError as error:
        raise RecommendationRequestError(str(error)) from error


def _positive(value):
    return value if type(value) is int and value > 0 else None


def _bool_or_none(value):
    return value if type(value) is bool else None


def _confidence(value):
    value = value.upper() if isinstance(value, str) else ''
    return value if value in ('HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT') else 'INSUFFICIENT'


def _capabilities(record):
    raw = record.get('capabilities') if isinstance(record.get('capabilities'), dict) else {}
    return Capabilities(**{name: _bool_or_none(raw.get(name)) for name in
                           ('chat', 'tools', 'thinking', 'coding', 'vision', 'instruction_following')})


def _fit_index(fit_snapshot):
    """Use a fit only when one current provider/id entry exists exactly once."""
    result = {}
    models = fit_snapshot.get('models') if isinstance(fit_snapshot.get('models'), list) else []
    for model in models:
        if not isinstance(model, dict):
            continue
        provider, identifier = model.get('provider'), model.get('id')
        if not isinstance(provider, str) or not isinstance(identifier, str):
            continue
        key = (provider, identifier)
        result.setdefault(key, []).append(model)
    return result


def _fit(record, match, models):
    source = record['source']
    key = (source['provider'], source['upstream_id'])
    candidates = models.get(key, ())
    model = candidates[0] if len(candidates) == 1 else None
    raw = model.get('fit') if isinstance(model, dict) and isinstance(model.get('fit'), dict) else {}
    capabilities = model.get('capabilities') if isinstance(model, dict) and isinstance(model.get('capabilities'), dict) else {}
    exact = match.get('match_status') == 'exact'
    return Fit(
        rating=raw.get('rating') if raw.get('rating') in ('EXCELLENT', 'GOOD', 'TIGHT', 'POOR', 'UNSUPPORTED', 'UNKNOWN') else 'UNKNOWN',
        confidence=_confidence(raw.get('confidence')),
        supported=_bool_or_none(capabilities.get('chat')),
        # A manifest digest proves the catalog artifact only for an exact join.
        revision=source['revision_digest'] if exact and model is not None else None,
        workload=WORKLOAD if model is not None else None,
        environment=ENVIRONMENT if model is not None else None,
        native_context=_positive(raw.get('native_context')),
        sustainable_context=_positive(raw.get('recommended_context')),
        headroom_bytes=_positive(raw.get('estimated_headroom_bytes')),
        conditional_offload=raw.get('gpu_offload') != 'none',
        policy_revision='fit-policy-v1' if model is not None else None,
        modality='text',
    )


def _absent_fit(record, raw):
    """Fit for a not-installed artifact from its revision-bound catalog fit record."""
    source = record['source']
    return Fit(
        rating=raw.get('rating') if raw.get('rating') in ('EXCELLENT', 'GOOD', 'TIGHT', 'POOR', 'UNSUPPORTED', 'UNKNOWN') else 'UNKNOWN',
        confidence=_confidence(raw.get('confidence')),
        supported=_bool_or_none(record.get('capabilities', {}).get('chat')),
        # saved_fit matched the exact catalog revision, so the pin is the artifact.
        revision=source['revision_digest'],
        workload=WORKLOAD, environment=ENVIRONMENT,
        native_context=_positive(raw.get('native_context')),
        sustainable_context=_positive(raw.get('recommended_context')),
        headroom_bytes=_positive(raw.get('estimated_headroom_bytes')),
        conditional_offload=raw.get('gpu_offload') != 'none',
        policy_revision='fit-policy-v1', modality='text')


def _absent_acquisition(record, disk, observed_at):
    """Download feasibility from the same disk accounting a real acquisition uses."""
    size = _positive(record.get('properties', {}).get('weight_size_bytes'))
    if not isinstance(disk, DiskSnapshot) or size is None or not observed_at:
        return Acquisition()
    need = disk_requirement(disk, size)
    if need['state'] not in ('PASS', 'FAIL'):
        return Acquisition()
    return Acquisition(status='FEASIBLE' if need['state'] == 'PASS' else 'BLOCKED',
                       download_bytes=size, incremental_bytes=size,
                       staging_bytes=need['transient_reserve'], reserve_bytes=need['safety_reserve'],
                       available_bytes=need['free_space'], observed_at=observed_at,
                       accounting_reference='nova_acquisition_disk_requirement')


def _identity(match, source):
    status = match.get('match_status')
    if status == 'exact':
        return True
    if status == 'not_installed':
        return isinstance(source.get('revision_digest'), str)
    if status == 'revision_mismatch':
        return False
    return None


def _candidate(record, fit_models, provider_available, catalog_revision,
               absent_fits=None, disk=None, observed_at=None):
    source = record['source']
    match = record.get('installed_match') if isinstance(record.get('installed_match'), dict) else {}
    installed = _bool_or_none(match.get('installed'))
    absent = match.get('match_status') == 'not_installed'
    raw_absent = (absent_fits or {}).get(record['catalog_id']) if absent else None
    return Candidate(
        catalog_id=record['catalog_id'],
        artifact=Artifact(source['provider'], source['upstream_id'], source['revision_digest']),
        fit=_absent_fit(record, raw_absent) if isinstance(raw_absent, dict) else _fit(record, match, fit_models),
        capabilities=_capabilities(record),
        installed=installed,
        provider_available=provider_available,
        identity_verified=_identity(match, source),
        storage_bytes=_positive(record.get('properties', {}).get('weight_size_bytes')),
        acquisition=_absent_acquisition(record, disk, observed_at) if absent else Acquisition(),
        # Catalog declarations are never task evidence; published guides are added
        # separately, only for the exact artifact they name.
        tasks=(),
        calibration=None,
        catalog_revision=catalog_revision,
        catalog_checked_at=record.get('metadata', {}).get('checked_at'),
    )


def _evidence_source(trust_class):
    """Map only named evidence provenance classes to evaluator source classes."""
    return {
        'LOCAL_OBSERVED': 'LOCAL_FACT',
        'TRUSTED_UPSTREAM': 'UPSTREAM_METADATA',
        'CURATED_NOVA': 'CURATED',
        'REPRODUCIBLE_BENCHMARK': 'REPRODUCIBLE_BENCHMARK',
    }.get(trust_class)


def _evidence_summary(record, policy_usable=False, limitation=None):
    """Safe public provenance summary; source references can remain API-internal."""
    return {
        'evidence_id': record['evidence_id'],
        'evidence_type': record['evidence_type'],
        'claim_kind': record['claim_kind'],
        'profiles': list(record['profiles']),
        'trust_class': record['trust_class'],
        'confidence_ceiling': confidence_ceiling(record),
        'source_version': record['source']['version'],
        'observed_at': record['source']['observed_at'],
        'published_at': record['source']['published_at'],
        'provenance_method': record['provenance']['method'],
        'corroborated': record['provenance']['corroborated'],
        'policy_usable': policy_usable,
        'limitation': limitation,
        # Attribution travels with the suggestion so the UI can always name its source.
        'attribution': (dict(record['recommendation'], url=record['source']['reference'],
                             retrieved_at=record['source']['observed_at'])
                        if record['evidence_type'] == 'published_recommendation' else None),
    }


def _exact_evidence_rows(store, candidate, request):
    """Return evidence only for the same catalog artifact and verified revision.

    Catalog tags or family names are never sufficient to transfer evidence.  The
    fit adapter sets a revision only for an exact installed manifest join, so a
    mismatched or uncertain artifact intentionally receives no policy evidence.
    """
    if not isinstance(store, EvidenceStore) or candidate.fit.revision != candidate.artifact.revision:
        return ()
    return store.for_model(candidate.catalog_id, request.profile,
                           provider=candidate.artifact.provider,
                           model_id=candidate.artifact.model_id,
                           revision=candidate.artifact.revision)


def _comparison_from_evidence(record, candidates, request):
    """Convert one already validated, exactly applicable curated comparison.

    Facts are deliberately not transformed into quality or task-reliability
    inputs.  Schema-v1 has no categorical task-suitability field, so only a
    validated curator comparison with matching workload/environment can be fed
    to the evaluator.  The current seed contains none.
    """
    if (record['evidence_type'] != 'curated_comparison' or
            record['claim_kind'] != 'QUALITY_JUDGMENT' or
            request.profile not in record['profiles'] or
            record['scope']['workload'] != request.workload or
            record['scope']['conditions'] != request.environment):
        return None
    source = _evidence_source(record['trust_class'])
    confidence = confidence_ceiling(record)
    if source is None or confidence == 'INSUFFICIENT':
        return None
    artifacts = tuple(Artifact(model['provider'], model['model_id'], model['revision'])
                      for model in record['models'])
    by_id = {candidate.catalog_id: candidate for candidate in candidates}
    for model, artifact in zip(record['models'], artifacts):
        candidate = by_id.get(model['catalog_id'])
        if candidate is None or candidate.artifact != artifact or candidate.fit.revision != artifact.revision:
            return None
    return Comparison(
        left=record['models'][0]['catalog_id'], right=record['models'][1]['catalog_id'],
        dimension=record['comparison']['dimension'], outcome=record['comparison']['outcome'],
        evidence=Evidence(reference=record['evidence_id'],
                          method=record['provenance']['method'], source=source,
                          claim_kind='QUALITY_JUDGMENT', artifacts=artifacts,
                          profile=request.profile, workload=request.workload,
                          confidence=confidence, applicable=True,
                          observed_at=record['source']['observed_at'],
                          corroborated=record['provenance']['corroborated'] is True,
                          supporting_references=tuple(record['comparison']['supporting_evidence_ids']),
                          environment=request.environment),
        explanation=record['comparison']['rationale'])


def _guide_task(record, candidate, request):
    """One attributed published-guide recommendation as LOW task-suitability proof."""
    if (record['evidence_type'] != 'published_recommendation' or
            record['claim_kind'] != 'QUALITY_JUDGMENT' or request.profile not in record['profiles'] or
            _evidence_source(record['trust_class']) != 'CURATED'):
        return None
    confidence = confidence_ceiling(record)
    if confidence == 'INSUFFICIENT':
        return None
    return TaskEvidence(
        evidence=Evidence(reference=record['evidence_id'], method=record['provenance']['method'],
                          source='CURATED', claim_kind='QUALITY_JUDGMENT',
                          artifacts=(candidate.artifact,), profile=request.profile,
                          # Guides describe ordinary local text use: NOVA's single workload.
                          workload=request.workload, confidence=min(
                              (confidence, 'LOW'), key=('INSUFFICIENT', 'LOW', 'MEDIUM', 'HIGH').index),
                          applicable=True, observed_at=record['source']['observed_at'],
                          corroborated=False, supporting_references=(record['source']['reference'],),
                          environment=request.environment),
        suitable=True)


def _with_guide_tasks(store, candidates, request):
    """Attach exact-artifact guide evidence; returns (candidates, usable evidence ids)."""
    updated, usable = [], set()
    for candidate in candidates:
        tasks = []
        for record in _exact_evidence_rows(store, candidate, request):
            task = _guide_task(record, candidate, request)
            if task is not None:
                tasks.append(task); usable.add(record['evidence_id'])
        updated.append(replace(candidate, tasks=tuple(tasks)) if tasks else candidate)
    return tuple(updated), usable


def _evidence_inputs(store, candidates, request, guide_usable=frozenset()):
    """Build detached summaries and the narrow policy-valid comparison tuple."""
    rows_by_candidate = {}
    unique = {}
    for candidate in candidates:
        rows = _exact_evidence_rows(store, candidate, request)
        rows_by_candidate[candidate.catalog_id] = rows
        for row in rows:
            unique[row['evidence_id']] = row
    comparisons = []
    usable = set(guide_usable)
    for record in unique.values():
        comparison = _comparison_from_evidence(record, candidates, request)
        if comparison is not None:
            comparisons.append(comparison)
            usable.add(record['evidence_id'])
    summaries = {}
    for catalog_id, rows in rows_by_candidate.items():
        summaries[catalog_id] = []
        for record in rows:
            if record['evidence_id'] in usable:
                summaries[catalog_id].append(_evidence_summary(record, True, None))
            elif record['claim_kind'] == 'FACT':
                summaries[catalog_id].append(_evidence_summary(
                    record, False,
                    'FACT does not establish task reliability, quality, or performance.'))
            else:
                summaries[catalog_id].append(_evidence_summary(
                    record, False,
                    'Judgment is not applicable to this exact workload, environment, or artifact revision.'))
    return summaries, tuple(sorted(comparisons, key=lambda item: item.evidence.reference))


def _evidence_status(store, comparisons):
    if not isinstance(store, EvidenceStore):
        return {'status': 'not_loaded', 'evidence_revision': None, 'record_count': 0,
                'loader_errors': [], 'automatic_refresh': False,
                'policy_comparison_count': 0}
    return {'status': store.status, 'evidence_revision': store.revision,
            'record_count': len(store.records), 'loader_errors': list(store.errors),
            'automatic_refresh': False, 'policy_comparison_count': len(comparisons)}


def _unavailable(request, code, message, catalog_snapshot, fit_snapshot=None):
    return {
        'schema_version': 1,
        'policy_version': '1',
        'profile': request.profile,
        'scope': request.scope,
        'request': asdict(request),
        'minimum_context': request.minimum_context or FIT_POLICY.minimum_context,
        'target_context': request.target_context or FIT_POLICY.target_context,
        'status': 'RECOMMENDATION_UNAVAILABLE',
        'confidence': 'INSUFFICIENT',
        'tiers': {name: {'candidates': [], 'abstention_reason': code}
                  for name in ('recommended', 'faster', 'max_quality', 'specialist')},
        'candidates': [],
        'excluded': [],
        'errors': [code],
        'catalog': {'status': catalog_snapshot.get('status'),
                    'catalog_revision': catalog_snapshot.get('catalog_revision'),
                    'record_count': catalog_snapshot.get('record_count', 0)},
        'fit_snapshot': {'provider_available': _bool_or_none((fit_snapshot or {}).get('provider_available'))},
        'limitations': [message],
    }
def build_recommendations(catalog_snapshot, fit_snapshot, request, evidence_store=None,
                          absent_fits=None, disk=None, observed_at=None):
    """Normalize supplied snapshots and evaluate without reading or mutating them."""
    if not isinstance(catalog_snapshot, dict) or not isinstance(fit_snapshot, dict):
        raise InputError('catalog and fit snapshots must be objects')
    status = catalog_snapshot.get('status')
    if status not in ('ready', 'partial'):
        return _unavailable(request, 'CATALOG_UNAVAILABLE',
                            'Catalog is unavailable or invalid; recommendation cannot be evaluated.',
                            catalog_snapshot, fit_snapshot)
    records = catalog_snapshot.get('models')
    if not isinstance(records, list):
        return _unavailable(request, 'CATALOG_RECORDS_UNAVAILABLE',
                            'Catalog records are unavailable; recommendation cannot be evaluated.',
                            catalog_snapshot, fit_snapshot)
    provider_available = _bool_or_none(fit_snapshot.get('provider_available'))
    candidates = tuple(_candidate(record, _fit_index(fit_snapshot), provider_available,
                                  catalog_snapshot.get('catalog_revision'),
                                  absent_fits, disk, observed_at)
                       for record in records if isinstance(record, dict) and
                       isinstance(record.get('catalog_id'), str) and
                       isinstance(record.get('source'), dict) and
                       isinstance(record['source'].get('provider'), str) and
                       isinstance(record['source'].get('upstream_id'), str) and
                       isinstance(record['source'].get('revision_digest'), str))
    candidates, guide_usable = _with_guide_tasks(evidence_store, candidates, request)
    evidence_rows, comparisons = _evidence_inputs(evidence_store, candidates, request, guide_usable)
    try:
        result = evaluate(candidates, request, comparisons=comparisons,
                          policy=Policy(minimum_context=FIT_POLICY.minimum_context,
                                        target_context=FIT_POLICY.target_context))
    except InputError as error:
        return _unavailable(request, 'NORMALIZATION_ERROR',
                            'Live recommendation inputs could not be normalized safely: ' + str(error),
                            catalog_snapshot, fit_snapshot)
    matches = {record['catalog_id']: record.get('installed_match') for record in records
               if isinstance(record, dict) and isinstance(record.get('catalog_id'), str)}
    provenance = {record['catalog_id']: {
        'source': record.get('source', {}).get('kind'),
        'reference': record.get('source', {}).get('reference'),
        'catalog_observed_at': record.get('metadata', {}).get('checked_at'),
        'method': record.get('metadata', {}).get('provenance', {}).get('method'),
    } for record in records if isinstance(record, dict) and isinstance(record.get('catalog_id'), str)}
    for row in result['candidates']:
        row['catalog_match'] = deepcopy(matches.get(row['catalog_id']))
        row['catalog_provenance'] = deepcopy(provenance.get(row['catalog_id']))
        row['evidence'] = deepcopy(evidence_rows.get(row['catalog_id'], []))
    for row in result['excluded']:
        row['catalog_match'] = deepcopy(matches.get(row['catalog_id']))
        row['catalog_provenance'] = deepcopy(provenance.get(row['catalog_id']))
        row['evidence'] = deepcopy(evidence_rows.get(row['catalog_id'], []))
    result['catalog'] = {
        'status': status,
        'catalog_revision': catalog_snapshot.get('catalog_revision'),
        'record_count': len(candidates),
        'loader_errors': list(catalog_snapshot.get('loader_errors', ())),
        'provenance_note': catalog_snapshot.get('provenance_note'),
    }
    result['fit_snapshot'] = {
        'provider_available': provider_available,
        'error': fit_snapshot.get('error') if isinstance(fit_snapshot.get('error'), str) else None,
        'model_count': len(fit_snapshot.get('models', ())) if isinstance(fit_snapshot.get('models'), list) else 0,
        'observed_runtime_available': any(isinstance(m, dict) and
                                          isinstance(m.get('observed_runtime'), dict) and
                                          m['observed_runtime'].get('available') is True
                                          for m in fit_snapshot.get('models', ())),
    }
    result['evidence'] = _evidence_status(evidence_store, comparisons)
    result['limitations'] += [
        'Catalog v1 capability declarations and tags are not quality, reliability or benchmark evidence.',
        'Evidence facts remain separate from quality judgments and do not establish task reliability.',
    ]
    if guide_usable:
        result['limitations'].append(
            'Suggestions rest on attributed published guides (LOW confidence), not NOVA benchmarks; fit is not speed or quality.')
    if not absent_fits:
        result['limitations'].append('Known non-installed artifacts have no revision-bound fit in this request.')
    if not comparisons and not guide_usable:
        result['limitations'].append(
            'No policy-valid task suitability or comparative evidence applies to this request, so tiers may abstain.')
    if isinstance(evidence_store, EvidenceStore) and evidence_store.status not in ('ready', 'partial'):
        result['limitations'].append('Evidence is unavailable or invalid; catalog and fit results remain advisory without it.')
    return result


def load_and_build_recommendations(catalog_snapshot, fit_snapshot, request):
    """Explicitly read the optional local seed, then run the pure normalizer.

    The loader is bounded and read-only.  It never refreshes evidence, calls a
    provider, changes active state, or touches catalog/model files.
    """
    store = load_evidence()
    # Bounded local reads: revision-bound fit records for absent catalog artifacts
    # and the provider storage filesystem's free space (statvfs, no writes).
    from acquisition_fit import saved_fit
    from acquisition_integration import storage_observation
    absent_fits = {}
    for record in catalog_snapshot.get('models', []) if isinstance(catalog_snapshot.get('models'), list) else []:
        match = record.get('installed_match') if isinstance(record, dict) else None
        if isinstance(match, dict) and match.get('match_status') == 'not_installed':
            raw = saved_fit(record, fit_snapshot) if isinstance(fit_snapshot.get('budget'), dict) else None
            if isinstance(raw, dict):
                absent_fits[record['catalog_id']] = raw
    disk, _ = storage_observation()
    observed = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    result = build_recommendations(catalog_snapshot, fit_snapshot, request, store,
                                   absent_fits=absent_fits, disk=disk, observed_at=observed)
    # Early catalog-unavailable returns still expose the optional evidence state.
    result.setdefault('evidence', _evidence_status(store, ()))
    if store.status not in ('ready', 'partial'):
        result.setdefault('limitations', []).append(
            'Evidence is unavailable or invalid; catalog and fit results remain advisory without it.')
    return result
