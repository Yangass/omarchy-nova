"""Optional evidence schema v1: bounded read-only load and pure validation/query.

No provider, evaluator, server or catalog imports. References are inert strings.
Validation establishes structure/provenance links, not the truth of measurements.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re

SCHEMA_VERSION = 1
DEFAULT_PATH = Path(__file__).parent.parent / 'catalog' / 'evidence.json'
MAX_BYTES = 1_048_576
MAX_RECORDS = 256
PROFILES = ('GENERAL', 'CODING', 'DEEP_REASONING', 'FAST_BACKGROUND', 'VISION')
TYPES = ('upstream_declared_capability', 'benchmark_result', 'local_calibration',
         'curated_comparison', 'observed_runtime_fact', 'published_recommendation')
# A published guide naming one model for a use: attributed, curated, never above LOW.
RECOMMENDATION_FIELDS = ('publisher', 'title', 'summary')
TRUST_CLASSES = ('LOCAL_OBSERVED', 'TRUSTED_UPSTREAM', 'CURATED_NOVA',
                 'REPRODUCIBLE_BENCHMARK', 'UNKNOWN')
CONFIDENCES = ('INSUFFICIENT', 'LOW', 'MEDIUM', 'HIGH')
CAPABILITIES = ('chat', 'tools', 'thinking', 'coding', 'vision', 'instruction_following')


def _object(value, allowed):
    if type(value) is not dict or set(value) - set(allowed):
        raise ValueError('expected object with supported fields')
    return value


def _text(value, required=False):
    if value is None and not required:
        return None
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise ValueError('expected nonempty bounded string or null')
    return value


def _identifier(value):
    value = _text(value, True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', value):
        raise ValueError('invalid evidence/catalog identifier')
    return value


def _enum(value, choices, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or value not in choices:
        raise ValueError('unsupported enumeration value')
    return value


def _strings(value, required=False):
    if type(value) is not list or len(value) > 32 or (required and not value):
        raise ValueError('expected bounded string list')
    result = [_text(v, True) for v in value]
    if len(set(result)) != len(result):
        raise ValueError('duplicate list entry')
    return result


def _timestamp(value):
    value = _text(value)
    if value is not None:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
            raise ValueError('expected UTC timestamp or null')
        datetime.fromisoformat(value)
    return value


def _model(value):
    value = _object(value, ('catalog_id', 'provider', 'model_id', 'revision', 'variant'))
    return {'catalog_id': _identifier(value.get('catalog_id')),
            'provider': _text(value.get('provider'), True),
            'model_id': _text(value.get('model_id'), True),
            'revision': _text(value.get('revision')), 'variant': _text(value.get('variant'))}


def _facts(value):
    value = _object(value, ('capabilities', 'properties'))
    caps = _object(value.get('capabilities', {}), CAPABILITIES)
    capabilities = {}
    for key in CAPABILITIES:
        v = caps.get(key)
        if v is not None and type(v) is not bool:
            raise ValueError('capability must be bool or null')
        capabilities[key] = v
    props = _object(value.get('properties', {}),
                    ('native_context', 'weight_size_bytes', 'parameter_count', 'architecture', 'quantization'))
    properties = {}
    for key in ('native_context', 'weight_size_bytes', 'parameter_count'):
        v = props.get(key)
        if v is not None and (type(v) is not int or v <= 0):
            raise ValueError('model property must be positive integer or null')
        properties[key] = v
    for key in ('architecture', 'quantization'):
        properties[key] = _text(props.get(key))
    return {'capabilities': capabilities, 'properties': properties}


def _measurement(value, benchmark):
    value = _object(value, ('benchmark', 'version', 'metric', 'value', 'direction', 'unit'))
    number = value.get('value')
    if number is not None and (type(number) not in (int, float) or
                               (type(number) is float and not math.isfinite(number))):
        raise ValueError('measurement value must be finite number or null')
    return {'benchmark': _text(value.get('benchmark'), benchmark),
            'version': _text(value.get('version')), 'metric': _text(value.get('metric'), True),
            'value': number, 'direction': _enum(value.get('direction'),
                ('higher_is_better', 'lower_is_better'), nullable=True),
            'unit': _text(value.get('unit'))}


def _record(value):
    v = _object(value, ('evidence_id', 'evidence_type', 'claim_kind', 'models', 'profiles',
        'source', 'trust_class', 'confidence', 'provenance', 'scope', 'facts',
        'measurement', 'comparison', 'recommendation', 'notes'))
    kind = _enum(v.get('evidence_type'), TYPES)
    judgment = kind == 'curated_comparison'
    published = kind == 'published_recommendation'
    claim = _enum(v.get('claim_kind'), ('QUALITY_JUDGMENT',) if judgment or published else ('FACT',))
    models = v.get('models')
    if type(models) is not list or len(models) != (2 if judgment else 1):
        raise ValueError('expected one fact subject or two comparison subjects')
    models = [_model(m) for m in models]
    if judgment and (models[0]['catalog_id'] == models[1]['catalog_id'] or
                    (models[0]['provider'], models[0]['model_id']) ==
                    (models[1]['provider'], models[1]['model_id'])):
        raise ValueError('comparison subjects must differ')
    profiles = _strings(v.get('profiles'), True)
    for profile in profiles:
        _enum(profile, PROFILES)
    source = _object(v.get('source'), ('reference', 'version', 'observed_at', 'published_at'))
    source = {'reference': _text(source.get('reference'), True),
              'version': _text(source.get('version')),
              'observed_at': _timestamp(source.get('observed_at')),
              'published_at': _timestamp(source.get('published_at'))}
    provenance = _object(v.get('provenance'), ('method', 'references', 'corroborated'))
    corroborated = provenance.get('corroborated')
    if corroborated is not None and type(corroborated) is not bool:
        raise ValueError('corroborated must be bool or null')
    provenance = {'method': _text(provenance.get('method'), True),
                  'references': _strings(provenance.get('references', [])),
                  'corroborated': corroborated}
    scope = _object(v.get('scope', {}), ('workload', 'conditions', 'comparison_group'))
    scope = {key: _text(scope.get(key), judgment) for key in
             ('workload', 'conditions', 'comparison_group')}
    payload = ('comparison' if judgment else 'recommendation' if published else
               'facts' if kind == 'upstream_declared_capability' else 'measurement')
    if any(v.get(key) is not None for key in ('facts', 'measurement', 'comparison', 'recommendation') if key != payload):
        raise ValueError('evidence has a conflicting payload')
    result = {'evidence_id': _identifier(v.get('evidence_id')), 'evidence_type': kind,
              'claim_kind': claim, 'models': models, 'profiles': profiles, 'source': source,
              'trust_class': _enum(v.get('trust_class'), TRUST_CLASSES),
              'confidence': _enum(v.get('confidence'), CONFIDENCES, nullable=True),
              'provenance': provenance, 'scope': scope, 'facts': None,
              'measurement': None, 'comparison': None, 'recommendation': None, 'notes': _text(v.get('notes'))}
    if judgment:
        c = _object(v.get('comparison'), ('dimension', 'outcome', 'supporting_evidence_ids', 'rationale'))
        result['comparison'] = {
            'dimension': _enum(c.get('dimension'), ('TASK', 'GENERAL_QUALITY', 'PERFORMANCE', 'SPECIALIST')),
            'outcome': _enum(c.get('outcome'), ('LEFT', 'RIGHT', 'EQUIVALENT')),
            'supporting_evidence_ids': [_identifier(i) for i in _strings(c.get('supporting_evidence_ids'), True)],
            'rationale': _text(c.get('rationale'), True)}
    elif published:
        r = _object(v.get('recommendation'), RECOMMENDATION_FIELDS)
        result['recommendation'] = {key: _text(r.get(key), True) for key in RECOMMENDATION_FIELDS}
        if result['trust_class'] != 'CURATED_NOVA' or result['confidence'] != 'LOW':
            raise ValueError('published recommendation must be CURATED_NOVA with LOW confidence')
        if not result['source']['observed_at'] or not re.match(r'https?://', result['source']['reference']):
            raise ValueError('published recommendation needs a retrieved URL and retrieval time')
        if result['provenance']['corroborated'] is True:
            raise ValueError('a single guide is not corroboration')
    elif payload == 'facts':
        result['facts'] = _facts(v.get('facts'))
    else:
        result['measurement'] = _measurement(v.get('measurement'), kind == 'benchmark_result')
    return result


def _check_support(record, index):
    """Require factual roots for judgments; references never create evidence."""
    comparison = record['comparison']
    support = [index.get(i) for i in comparison['supporting_evidence_ids']]
    if any(r is None or r['evidence_type'] not in ('benchmark_result', 'local_calibration') for r in support):
        raise ValueError('comparison requires existing benchmark/calibration facts')
    for subject in record['models']:
        if not any(subject in r['models'] for r in support):
            raise ValueError('comparison evidence does not cover both exact subjects')
    for fact in support:
        if (fact['scope'] != record['scope'] or not set(record['profiles']) <= set(fact['profiles']) or
                fact['measurement']['value'] is None or fact['measurement']['direction'] is None):
            raise ValueError('comparison evidence has incompatible or unknown scope/metric')
    # Schema v1 only admits comparisons over one explicitly shared measurement
    # definition. Cross-suite synthesis needs a future explicit schema decision.
    signatures = {tuple(r['measurement'][k] for k in ('benchmark', 'version', 'metric', 'direction', 'unit'))
                  for r in support}
    if len(signatures) != 1 or any(r['measurement']['version'] is None for r in support):
        raise ValueError('unrelated or unversioned measurements cannot support a comparison')


@dataclass(frozen=True)
class EvidenceStore:
    status: str
    revision: str | None
    _records: tuple[dict, ...]
    errors: tuple[str, ...]

    @property
    def records(self):
        return deepcopy(self._records)

    def for_model(self, catalog_id, profile=None, *, provider=None, model_id=None, revision=None):
        """Exact lookup only. Omitted filters do not certify artifact applicability."""
        _identifier(catalog_id)
        if profile is not None:
            _enum(profile, PROFILES)
        return tuple(deepcopy(r) for r in self._records if
                     (profile is None or profile in r['profiles']) and any(
                         m['catalog_id'] == catalog_id and
                         (provider is None or m['provider'] == provider) and
                         (model_id is None or m['model_id'] == model_id) and
                         (revision is None or m['revision'] == revision)
                         for m in r['models']))


def confidence_ceiling(record):
    """Claim-confidence bound only; never a quality score or recommendation."""
    if record['trust_class'] == 'UNKNOWN' or record['confidence'] is None:
        return 'INSUFFICIENT'
    levels = [record['confidence']]
    if record['provenance']['corroborated'] is not True:
        levels.append('MEDIUM')
    if record['source']['observed_at'] is None:
        levels.append('LOW')
    return min(levels, key=CONFIDENCES.index)


def validate_evidence(value):
    """Pure normalization, isolated record rejection and second-pass references."""
    try:
        v = _object(value, ('schema_version', 'evidence_revision', 'records'))
        if type(v.get('schema_version')) is not int or v['schema_version'] != SCHEMA_VERSION:
            raise ValueError('unsupported evidence schema version')
        revision = _text(v.get('evidence_revision'), True)
        rows = v.get('records')
        if type(rows) is not list or len(rows) > MAX_RECORDS:
            raise ValueError('expected bounded records list')
    except ValueError as error:
        return EvidenceStore('invalid', None, (), (str(error),))
    candidates, errors = [], []
    for i, row in enumerate(rows):
        try:
            candidates.append((i, _record(row)))
        except ValueError as error:
            errors.append(f'record[{i}]: {error}')
    counts = Counter(r['evidence_id'] for _, r in candidates)
    unique = []
    for i, r in candidates:
        if counts[r['evidence_id']] != 1:
            errors.append(f'record[{i}]: duplicate evidence_id')
        else:
            unique.append((i, r))
    index = {r['evidence_id']: r for _, r in unique}
    records = []
    for i, r in unique:
        try:
            # Comparisons need factual roots; a published recommendation is its own attributed root.
            if r['evidence_type'] == 'curated_comparison':
                _check_support(r, index)
            records.append(r)
        except ValueError as error:
            errors.append(f'record[{i}]: {error}')
    records.sort(key=lambda r: r['evidence_id'])
    status = 'partial' if errors and records else 'invalid' if errors else 'ready'
    return EvidenceStore(status, revision, tuple(records), tuple(errors))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('non-finite JSON constant')


def load_evidence(path=DEFAULT_PATH):
    """Only explicit bounded local reading; import and queries perform no I/O."""
    try:
        with Path(path).open('rb') as file:
            raw = file.read(MAX_BYTES + 1)
    except OSError:
        return EvidenceStore('unavailable', None, (), ('evidence file unavailable',))
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError('evidence size limit')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        return validate_evidence(value)
    except (ValueError, RecursionError):
        return EvidenceStore('invalid', None, (), ('invalid evidence encoding/JSON or size limit',))
