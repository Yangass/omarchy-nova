"""Optional local model knowledge. No provider, network, fit, or runtime imports."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'catalog' / 'models.json'
MAX_BYTES = 1_048_576
MAX_RECORDS = 256
CAPABILITIES = ('chat', 'tools', 'thinking', 'coding', 'vision', 'instruction_following')
TASK_TAGS = frozenset(('general', 'coding', 'reasoning', 'fast', 'vision'))
SOURCE_KINDS = frozenset(('local_registry_snapshot', 'upstream_metadata', 'manual_curated'))
ACQUISITION_SOURCES = frozenset(('ollama_registry', 'ollama_registry_requested'))


@dataclass(frozen=True)
class Catalog:
    status: str
    revision: str | None
    records: tuple[dict[str, Any], ...]
    errors: tuple[str, ...] = ()

    def match_installed(self, inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Exact case-sensitive provider/id join; never merge into either input.

        Alias matching only identifies a record, not proof an artifact is unchanged.
        Revision identity is unverified unless the caller supplies a digest.
        """
        index = {(m['source']['provider'], m['source']['upstream_id']): m for m in self.records}
        matches = []
        for item in inventory:
            if not isinstance(item, dict): continue
            provider, identifier = item.get('provider'), item.get('id')
            if not isinstance(provider, str) or not isinstance(identifier, str): continue
            if item.get('installed') is not True: continue
            record = index.get((provider, identifier))
            digest = record['source']['revision_digest'] if record else None
            supplied = item.get('digest')
            matches.append({'provider': provider, 'id': identifier,
                            'catalog_id': record['catalog_id'] if record else None,
                            'revision_match': supplied == digest if isinstance(supplied, str) and digest else None})
        return matches

    def get(self, catalog_id: str) -> dict[str, Any] | None:
        """Return a detached record for an internal caller."""
        return deepcopy(next((m for m in self.records if m['catalog_id'] == catalog_id), None))


def _object(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('invalid object or unsupported fields')
    return value


def _text(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required: return None
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError('expected nonempty string or null')
    return value


def _positive(value: Any) -> int | None:
    if value is None: return None
    if type(value) is not int or value <= 0: raise ValueError('expected positive integer or null')
    return value


def _strings(value: Any, *, nullable: bool = False) -> list[str] | None:
    if value is None and nullable: return None
    if not isinstance(value, list) or len(value) > 32: raise ValueError('expected short string list')
    result = [_text(v, required=True) for v in value]
    if len(set(result)) != len(result): raise ValueError('duplicate list value')
    return result


def _acquisition(value: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize optional acquisition facts; trust is decided outside the catalog.

    A catalog can state an inert source class but cannot mark itself trusted. The
    integration layer compares the class against backend-owned allowlists.
    """
    if value is None:
        return None
    raw = _object(value, {'provider', 'artifact_id', 'exact_tag', 'expected_revision',
                          'expected_size_bytes', 'source', 'provenance',
                          'minimum_provider_version'})
    provider = _text(raw.get('provider'), required=True)
    artifact = _text(raw.get('artifact_id'), required=True)
    tag = _text(raw.get('exact_tag'), required=True)
    revision = _text(raw.get('expected_revision'), required=True)
    size = _positive(raw.get('expected_size_bytes'))
    source_id = _text(raw.get('source'), required=True)
    if source_id not in ACQUISITION_SOURCES:
        raise ValueError('unsupported acquisition source class')
    if provider != source['provider'] or artifact != source['upstream_id']:
        raise ValueError('acquisition provider/artifact must exactly match catalog source')
    if source['revision_digest'] is None or revision != source['revision_digest']:
        raise ValueError('acquisition revision must exactly match catalog source digest')
    if size != source.get('_weight_size_bytes'):
        raise ValueError('acquisition size must exactly match catalog artifact size')
    if ':' not in artifact or tag != artifact.rsplit(':', 1)[1]:
        raise ValueError('acquisition tag must exactly match explicit artifact tag')
    provenance = _object(raw.get('provenance'), {'method', 'observed_at', 'notes'})
    observed = _text(provenance.get('observed_at'), required=True)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', observed):
        raise ValueError('acquisition provenance observed_at must be UTC timestamp')
    datetime.fromisoformat(observed.replace('Z', '+00:00'))
    normalized = {'provider': provider, 'artifact_id': artifact, 'exact_tag': tag,
                  'expected_revision': revision, 'expected_size_bytes': size,
                  'source': source_id,
                  'provenance': {'method': _text(provenance.get('method'), required=True),
                                 'observed_at': observed, 'notes': _text(provenance.get('notes'))}}
    minimum = raw.get('minimum_provider_version')
    if minimum is not None:
        # Same shape providers.parse_version reads; checked here to keep the catalog provider-free.
        if not isinstance(minimum, str) or not re.fullmatch(r'\d+\.\d+\.\d+', minimum):
            raise ValueError('minimum_provider_version must be a dotted version like 0.30.0')
        normalized['minimum_provider_version'] = minimum
    return normalized


def _record(value: Any) -> tuple[dict[str, Any], str | None]:
    r = _object(value, {'catalog_id', 'display_name', 'family', 'release_label', 'source',
                        'properties', 'capabilities', 'task_tags', 'metadata', 'acquisition'})
    identifier = _text(r.get('catalog_id'), required=True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', identifier): raise ValueError('invalid catalog_id')
    source = _object(r.get('source'), {'kind', 'provider', 'upstream_id', 'reference', 'revision_digest'})
    kind = _text(source.get('kind'), required=True)
    if kind not in SOURCE_KINDS: raise ValueError('unknown source kind')
    source = {key: _text(source.get(key), required=key != 'revision_digest')
              for key in ('kind', 'provider', 'upstream_id', 'reference', 'revision_digest')}
    props = _object(r.get('properties', {}), {'architecture', 'parameter_count', 'quantizations', 'weight_size_bytes', 'native_context'})
    properties = {key: _positive(props.get(key)) for key in ('parameter_count', 'weight_size_bytes', 'native_context')}
    properties.update(architecture=_text(props.get('architecture')), quantizations=_strings(props.get('quantizations'), nullable=True))
    caps = _object(r.get('capabilities', {}), set(CAPABILITIES))
    capabilities = {}
    for key in CAPABILITIES:
        cap = caps.get(key)
        if cap is not None and type(cap) is not bool: raise ValueError('capabilities must be true, false or null')
        capabilities[key] = cap
    tags = _strings(r.get('task_tags', []))
    if set(tags) - TASK_TAGS: raise ValueError('unknown task tag')
    metadata = _object(r.get('metadata'), {'release_date', 'license', 'checked_at', 'provenance'})
    release = _text(metadata.get('release_date'))
    if release is not None:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', release): raise ValueError('invalid release date')
        date.fromisoformat(release)
    checked = _text(metadata.get('checked_at'))
    if checked is not None:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', checked): raise ValueError('checked_at must be UTC timestamp or null')
        datetime.fromisoformat(checked.replace('Z', '+00:00'))
    provenance = _object(metadata.get('provenance'), {'method', 'references', 'notes'})
    provenance = {'method': _text(provenance.get('method'), required=True),
                  'references': _strings(provenance.get('references', [])), 'notes': _text(provenance.get('notes'))}
    acquisition_source = dict(source, _weight_size_bytes=properties['weight_size_bytes'])
    try:
        acquisition = _acquisition(r.get('acquisition'), acquisition_source)
        acquisition_error = None
    except ValueError as error:
        acquisition, acquisition_error = None, str(error)
    return {'catalog_id': identifier, 'display_name': _text(r.get('display_name'), required=True),
            'family': _text(r.get('family')), 'release_label': _text(r.get('release_label')),
            'source': source, 'properties': properties, 'capabilities': capabilities, 'task_tags': tags,
            'metadata': {'release_date': release, 'license': _text(metadata.get('license')),
                         'checked_at': checked, 'provenance': provenance},
            'acquisition': acquisition}, acquisition_error


RULE_TASK_TAGS = frozenset(('general', 'coding', 'vision', 'reasoning'))
CITED_TAG_MESSAGES = ('general', 'coding')


def tag_rule_violations(record: dict) -> list[str]:
    """Check one record against the catalog's tag rule; never raises on a malformed record."""
    if not isinstance(record, dict):
        record = {}
    identifier = record.get('catalog_id')
    if not isinstance(identifier, str) or not identifier.strip():
        identifier = '?'
    tags = record.get('task_tags')
    tags = tags if isinstance(tags, list) else []
    tags = [tag for tag in tags if isinstance(tag, str)]
    capabilities = record.get('capabilities')
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    provenance = record.get('metadata')
    provenance = provenance.get('provenance') if isinstance(provenance, dict) else None
    references = provenance.get('references') if isinstance(provenance, dict) else None
    references = references if isinstance(references, list) else []
    messages = [f'{identifier}: unknown task tag \'{tag}\'' for tag in sorted(set(tags) - RULE_TASK_TAGS)]
    for tag in CITED_TAG_MESSAGES:
        if tag in tags and not any(isinstance(ref, str) and ref.strip() for ref in references):
            messages.append(f"{identifier}: '{tag}' needs a cited reference")
    if ('vision' in tags) != (capabilities.get('vision') is True):
        messages.append(f"{identifier}: 'vision' must mirror capabilities.vision")
    if ('reasoning' in tags) != (capabilities.get('thinking') is True):
        messages.append(f"{identifier}: 'reasoning' must mirror capabilities.thinking")
    return messages


def validate_catalog(value: Any) -> Catalog:
    """Reject bad envelopes; isolate bad records and all ambiguous identities."""
    try:
        document = _object(value, {'schema_version', 'catalog_revision', 'models'})
        if type(document.get('schema_version')) is not int or document['schema_version'] != SCHEMA_VERSION:
            raise ValueError('unsupported catalog schema version')
        revision = _text(document.get('catalog_revision'), required=True)
        models = document.get('models')
        if not isinstance(models, list) or len(models) > MAX_RECORDS:
            raise ValueError('models must be a bounded list')
    except ValueError as error:
        return Catalog('invalid', None, (), (str(error),))
    candidates, errors = [], []
    for index, model in enumerate(models):
        try:
            normalized, acquisition_error = _record(model)
            candidates.append((index, normalized))
            if acquisition_error:
                errors.append(f'record[{index}].acquisition: {acquisition_error}')
        except (ValueError, TypeError) as error: errors.append(f'record[{index}]: {error}')
    ids = Counter(m['catalog_id'] for _, m in candidates)
    sources = Counter((m['source']['provider'], m['source']['upstream_id']) for _, m in candidates)
    records = []
    for index, model in candidates:
        if ids[model['catalog_id']] > 1 or sources[(model['source']['provider'], model['source']['upstream_id'])] > 1:
            errors.append(f'record[{index}]: duplicate catalog or source identity')
        else: records.append(model)
    status = 'partial' if errors and records else 'invalid' if errors else 'ready'
    return Catalog(status, revision, tuple(records), tuple(errors))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def load_catalog(path: Path | str = DEFAULT_PATH) -> Catalog:
    """Explicit bounded read only. Importing does not load the catalog or contact services."""
    try:
        with Path(path).open('rb') as file: raw = file.read(MAX_BYTES + 1)
    except OSError:
        return Catalog('unavailable', None, (), ('catalog file unavailable',))
    try:
        if len(raw) > MAX_BYTES: raise ValueError('catalog exceeds size limit')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        return validate_catalog(value)
    except (ValueError, RecursionError):
        return Catalog('invalid', None, (), ('invalid catalog encoding/JSON or size limit',))
