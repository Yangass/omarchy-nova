"""Evidence foundation vectors A–M; existing discovery suite supplies N."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import evidence

SEED = Path(__file__).parent.parent / 'catalog' / 'evidence.json'
CATALOG = Path(__file__).parent.parent / 'catalog' / 'models.json'


def subject(identifier):
    return {'catalog_id': identifier, 'provider': 'local', 'model_id': identifier,
            'revision': 'revision-' + identifier, 'variant': None}


def measurement(identifier='a', suite='fixture-coding', direction='higher_is_better'):
    return {
        'evidence_id': identifier + '-measurement', 'evidence_type': 'benchmark_result',
        'claim_kind': 'FACT', 'models': [subject(identifier)], 'profiles': ['CODING'],
        'source': {'reference': 'fixture://report', 'version': 'report-v1',
                   'observed_at': '2026-09-08T12:00:00Z', 'published_at': None},
        'trust_class': 'REPRODUCIBLE_BENCHMARK', 'confidence': 'MEDIUM',
        'provenance': {'method': 'synthetic-test-protocol', 'references': [], 'corroborated': False},
        'scope': {'workload': 'fixture-coding', 'conditions': 'fixture-settings-v1',
                  'comparison_group': 'fixture-comparison-v1'},
        'measurement': {'benchmark': suite, 'version': 'suite-v1', 'metric': 'pass_rate',
                        'value': 75.5, 'direction': direction, 'unit': 'percent'},
    }


def judgment():
    value = measurement()
    value.update(evidence_id='a-vs-b', evidence_type='curated_comparison',
                 claim_kind='QUALITY_JUDGMENT', models=[subject('a'), subject('b')],
                 trust_class='CURATED_NOVA', measurement=None,
                 comparison={'dimension': 'TASK', 'outcome': 'LEFT',
                    'supporting_evidence_ids': ['a-measurement', 'b-measurement'],
                    'rationale': 'Synthetic fixture only: the scoped report supports the left candidate.'})
    return value


def document(*records):
    return {'schema_version': 1, 'evidence_revision': 'fixture-v1', 'records': list(records)}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.seed = json.loads(SEED.read_text())

    def test_A_seed_loads_and_matches_existing_catalog_without_inference(self):
        store = evidence.load_evidence(SEED)
        self.assertEqual(store.status, 'ready')
        self.assertFalse(store.errors)
        facts = [r for r in store.records if r['evidence_type'] == 'upstream_declared_capability']
        self.assertEqual(len(facts), 4)  # the transcribed seed facts; guide records are checked separately
        catalog = json.loads(CATALOG.read_text())
        by_id = {r['catalog_id']: r for r in catalog['models']}
        for row in facts:
            model = row['models'][0]
            original = by_id[model['catalog_id']]
            self.assertEqual(model['model_id'], original['source']['upstream_id'])
            self.assertEqual(model['provider'], original['source']['provider'])
            self.assertEqual(model['revision'], original['source']['revision_digest'])
            # Evidence was transcribed from the original seed snapshot; the catalog has since grown.
            self.assertEqual(row['source']['version'], '2026-09-08.seed1')
            self.assertEqual(row['source']['observed_at'], original['metadata']['checked_at'])
            self.assertEqual(row['facts']['capabilities'], original['capabilities'])
            for name in ('native_context', 'weight_size_bytes', 'parameter_count', 'architecture'):
                self.assertEqual(row['facts']['properties'][name], original['properties'][name])
            self.assertEqual(row['facts']['properties']['quantization'], original['properties']['quantizations'][0])
            self.assertEqual(row['claim_kind'], 'FACT')
            self.assertIsNone(row['comparison'])

    def test_B_bad_record_isolated(self):
        self.seed['records'].append({'evidence_id': 'broken', 'quality': 9.2})
        store = evidence.validate_evidence(self.seed)
        self.assertEqual(store.status, 'partial')
        self.assertEqual(len(store.records), len(self.seed['records']) - 1)
        self.assertEqual(len(store.errors), 1)

    def test_C_unsupported_schema_clean(self):
        for version in (True, None, '1', 2):
            self.seed['schema_version'] = version
            result = evidence.validate_evidence(self.seed)
            self.assertEqual(result.status, 'invalid')
            self.assertEqual(result.records, ())

    def test_D_null_and_omitted_values_remain_unknown(self):
        row = self.seed['records'][0]
        row['facts'] = {}
        row['confidence'] = None
        row['source']['observed_at'] = None
        row['models'][0]['revision'] = None
        row['scope'] = {}
        result = evidence.validate_evidence(document(row)).records[0]
        self.assertTrue(all(value is None for value in result['facts']['capabilities'].values()))
        self.assertTrue(all(value is None for value in result['facts']['properties'].values()))
        self.assertIsNone(result['models'][0]['revision'])
        self.assertIsNone(result['confidence'])
        self.assertEqual(evidence.confidence_ceiling(result), 'INSUFFICIENT')
        row = measurement()
        row['measurement'].update(value=None, direction=None, version=None)
        result = evidence.validate_evidence(document(row)).records[0]
        self.assertIsNone(result['measurement']['value'])
        self.assertIsNone(result['measurement']['version'])

    def test_E_names_do_not_create_capability_or_quality(self):
        row = self.seed['records'][0]
        row['models'] = [subject('fastest-coder-9999b')]
        row['facts'] = {}
        store = evidence.validate_evidence(document(row))
        found = store.for_model('fastest-coder-9999b', 'CODING')
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]['facts']['capabilities']['coding'])
        self.assertIsNone(found[0]['comparison'])

    def test_F_trust_preserved_and_never_creates_quality(self):
        for trust in evidence.TRUST_CLASSES:
            row = self.seed['records'][0]
            row['trust_class'] = trust
            store = evidence.validate_evidence(document(row))
            self.assertEqual(store.records[0]['trust_class'], trust)
            self.assertIsNone(store.records[0]['comparison'])
        self.assertEqual(evidence.confidence_ceiling(store.records[0]), 'INSUFFICIENT')

    def test_G_benchmark_definition_preserved(self):
        for direction in ('higher_is_better', 'lower_is_better'):
            raw = measurement(direction=direction)
            row = evidence.validate_evidence(document(raw)).records[0]
            self.assertEqual(row['measurement'], raw['measurement'])
            self.assertEqual(row['source']['version'], 'report-v1')
            self.assertEqual(row['claim_kind'], 'FACT')

    def test_H_unrelated_benchmarks_never_combined(self):
        a, b = measurement(), measurement('b', suite='unrelated-suite')
        result = evidence.validate_evidence(document(a, b))
        self.assertEqual(len(result.records), 2)
        self.assertTrue(all(r['comparison'] is None for r in result.records))
        result = evidence.validate_evidence(document(a, b, judgment()))
        self.assertEqual(result.status, 'partial')
        self.assertEqual(len(result.records), 2)
        self.assertIn('unrelated', result.errors[0])

    def test_I_lookup_is_model_task_and_revision_scoped(self):
        store = evidence.validate_evidence(document(measurement()))
        self.assertEqual(len(store.for_model('a', 'CODING', provider='local', model_id='a', revision='revision-a')), 1)
        self.assertEqual(store.for_model('a', 'GENERAL'), ())
        self.assertEqual(store.for_model('a', 'CODING', provider='other'), ())
        self.assertEqual(store.for_model('a', 'CODING', revision='revision-b'), ())
        self.assertEqual(store.for_model('a', 'CODING', model_id='A'), ())
        with self.assertRaises(ValueError):
            store.for_model('a', 'MAGIC')

    def test_J_absent_evidence_absent(self):
        store = evidence.load_evidence(SEED)
        self.assertEqual(store.for_model('missing', 'GENERAL'), ())
        self.assertTrue(all(r['measurement'] is None and r['comparison'] is None for r in store.records))
        empty = evidence.validate_evidence(document())
        self.assertEqual(empty.status, 'ready')
        self.assertEqual(empty.records, ())

    def test_K_evaluator_remains_deterministic_and_disconnected(self):
        from recommendation import Request, evaluate
        req = Request('fixture-workload', 'fixture-environment')
        before = evaluate((), req)
        evidence.validate_evidence(self.seed)
        self.assertEqual(evaluate((), req), before)
        self.assertEqual(before['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_L_existing_integration_still_abstains(self):
        from recommendation_integration import build_recommendations, parse_request
        catalog = json.loads(CATALOG.read_text())
        records = deepcopy(catalog['models'])
        models = []
        for r in records:
            r['installed_match'] = {'installed': True, 'match_status': 'exact'}
            models.append({'provider': r['source']['provider'], 'id': r['source']['upstream_id'],
                'capabilities': {'chat': True}, 'fit': {'rating': 'EXCELLENT', 'confidence': 'medium',
                    'native_context': 131072, 'recommended_context': 32768, 'gpu_offload': 'full_estimated'}})
        view = {'status': 'ready', 'models': records, 'catalog_revision': catalog['catalog_revision']}
        snapshot = {'provider_available': True, 'models': models}
        for scope in ('installed', 'known'):
            request = parse_request('scope=' + scope)
            before = build_recommendations(view, snapshot, request)
            evidence.load_evidence(SEED)
            after = build_recommendations(view, snapshot, request)
            self.assertEqual(before, after)
            self.assertEqual(after['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
            self.assertEqual(after['confidence'], 'INSUFFICIENT')

    def test_M_load_only_reads_and_queries_have_no_io(self):
        before = deepcopy(self.seed)
        raw = SEED.read_bytes()
        original = Path.open
        def readonly(path, mode='r', *args, **kwargs):
            self.assertEqual(mode, 'rb')
            self.assertEqual(path, SEED)
            return original(path, mode, *args, **kwargs)
        with ExitStack() as stack:
            for name in ('socket.socket', 'urllib.request.urlopen', 'subprocess.Popen', 'os.system',
                         'os.remove', 'os.rename', 'pathlib.Path.write_text', 'pathlib.Path.write_bytes'):
                stack.enter_context(patch(name, side_effect=AssertionError('side effect forbidden')))
            stack.enter_context(patch.object(Path, 'open', readonly))
            store = evidence.load_evidence(SEED)
            with patch.object(Path, 'open', side_effect=AssertionError('query/validation I/O forbidden')):
                evidence.validate_evidence(self.seed)
                store.for_model(store.records[0]['models'][0]['catalog_id'], 'CODING')
        self.assertEqual(self.seed, before)
        self.assertEqual(SEED.read_bytes(), raw)

    def test_curated_comparison_requires_fact_roots_and_explicit_scope(self):
        a, b, c = measurement(), measurement('b'), judgment()
        b['measurement']['value'] = 60
        store = evidence.validate_evidence(document(c, a, b))
        self.assertEqual(store.status, 'ready')
        self.assertEqual(store.for_model('a', 'CODING')[1]['evidence_id'], 'a-vs-b')
        self.assertEqual(store.records, evidence.validate_evidence(document(b, a, c)).records)
        for field, bad in [('scope', {}), ('comparison', {'dimension': 'TASK', 'outcome': 'LEFT'})]:
            value = deepcopy(c)
            value[field] = bad
            self.assertEqual(evidence.validate_evidence(document(a, b, value)).status, 'partial')
        for refs in (['missing'], ['a-vs-b'], ['a-measurement']):
            value = deepcopy(c)
            value['comparison']['supporting_evidence_ids'] = refs
            self.assertEqual(len(evidence.validate_evidence(document(a, b, value)).records), 2)

    def test_declarations_cannot_back_a_quality_judgment(self):
        row = deepcopy(self.seed['records'][0])
        c = judgment()
        c['comparison']['supporting_evidence_ids'] = [row['evidence_id']]
        store = evidence.validate_evidence(document(row, c))
        self.assertEqual(len(store.records), 1)
        self.assertEqual(store.status, 'partial')

    def test_comparison_rejects_variant_scope_version_and_metric_mismatch(self):
        for mutate in (
            lambda r: r['models'][0].update(revision='different'),
            lambda r: r['models'][0].update(variant='different'),
            lambda r: r['scope'].update(conditions='different'),
            lambda r: r['measurement'].update(version='different'),
            lambda r: r['measurement'].update(version=None),
            lambda r: r['measurement'].update(value=None),
            lambda r: r['measurement'].update(direction='lower_is_better'),
            lambda r: r.update(profiles=['GENERAL']),
        ):
            b = measurement('b')
            mutate(b)
            store = evidence.validate_evidence(document(measurement(), b, judgment()))
            self.assertEqual(len(store.records), 2)

    def test_duplicates_all_excluded_and_dependent_judgments_removed(self):
        store = evidence.validate_evidence(document(measurement(), measurement(), measurement('b'), judgment()))
        self.assertEqual([r['evidence_id'] for r in store.records], ['b-measurement'])
        self.assertEqual(len(store.errors), 3)

    def test_false_zero_not_unknown_and_no_type_coercion(self):
        row = self.seed['records'][0]
        row['facts']['capabilities']['vision'] = False
        self.assertIs(evidence.validate_evidence(document(row)).records[0]['facts']['capabilities']['vision'], False)
        for bad in ('false', 0, 1, [], {}):
            row['facts']['capabilities']['vision'] = bad
            self.assertEqual(evidence.validate_evidence(document(row)).status, 'invalid')
        raw = measurement()
        raw['measurement']['value'] = 0
        self.assertEqual(evidence.validate_evidence(document(raw)).records[0]['measurement']['value'], 0)
        for bad in (True, '75', float('nan'), float('inf')):
            raw['measurement']['value'] = bad
            self.assertEqual(evidence.validate_evidence(document(raw)).status, 'invalid')

    def test_missing_bad_json_size_encoding_and_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            self.assertEqual(evidence.load_evidence(path).status, 'unavailable')
            self.assertEqual(evidence.load_evidence(directory).status, 'unavailable')
            for raw in (b'{', b'\xff', b'{"schema_version":1,"schema_version":1}',
                        b' ' * (evidence.MAX_BYTES + 1), b'[' * 2000, b'NaN'):
                path.write_bytes(raw)
                self.assertEqual(evidence.load_evidence(path).status, 'invalid')
        for bad in ('yesterday', '2026-02-31T00:00:00Z'):
            row = measurement()
            row['source']['observed_at'] = bad
            self.assertEqual(evidence.validate_evidence(document(row)).status, 'invalid')

    def test_detached_queries_records_and_input(self):
        before = deepcopy(self.seed)
        store = evidence.validate_evidence(self.seed)
        rows = store.records
        rows[0]['models'][0]['revision'] = 'changed'
        fact = next(r for r in before['records'] if r['evidence_type'] == 'upstream_declared_capability')
        result = [r for r in store.for_model(fact['models'][0]['catalog_id']) if r['facts']]
        result[0]['facts']['capabilities']['coding'] = True
        self.assertEqual(self.seed, before)
        self.assertTrue(all(r['facts']['capabilities']['coding'] is None for r in store.records if r['facts']))
        self.assertNotEqual(store.records[0]['models'][0]['revision'], 'changed')

    def test_shipped_guides_are_attributed_low_and_name_exact_catalog_artifacts(self):
        store = evidence.load_evidence(SEED)
        catalog = {r['catalog_id']: r for r in json.loads(CATALOG.read_text())['models']}
        guides = [r for r in store.records if r['evidence_type'] == 'published_recommendation']
        self.assertTrue(guides)
        for row in guides:
            model = row['models'][0]
            self.assertEqual(model['revision'], catalog[model['catalog_id']]['source']['revision_digest'])
            self.assertEqual((row['trust_class'], evidence.confidence_ceiling(row)), ('CURATED_NOVA', 'LOW'))
            self.assertTrue(row['source']['reference'].startswith('https://') and row['recommendation']['publisher'])

    def test_confidence_ceiling_uses_provenance_without_quality_promotion(self):
        raw = measurement()
        raw['confidence'] = 'HIGH'
        row = evidence.validate_evidence(document(raw)).records[0]
        self.assertEqual(evidence.confidence_ceiling(row), 'MEDIUM')
        row['provenance']['corroborated'] = True
        self.assertEqual(evidence.confidence_ceiling(row), 'HIGH')
        row['source']['observed_at'] = None
        self.assertEqual(evidence.confidence_ceiling(row), 'LOW')
        row['trust_class'] = 'UNKNOWN'
        self.assertEqual(evidence.confidence_ceiling(row), 'INSUFFICIENT')
        self.assertIsNone(row['comparison'])


if __name__ == '__main__':
    unittest.main()
