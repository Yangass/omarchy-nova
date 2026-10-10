"""B2 normalization/API tests; all live inputs are fixture snapshots."""
from contextlib import ExitStack
from copy import deepcopy
import io
import json
import socket
import unittest
from unittest.mock import patch
import urllib.request

import evidence
import recommendation_integration as integration
import server


def record(identifier, match='exact', installed=True):
    return {
        'catalog_id': identifier.replace(':', '-'),
        'source': {'kind': 'local_registry_snapshot', 'provider': 'ollama',
                   'upstream_id': identifier, 'reference': 'local-reference:' + identifier,
                   'revision_digest': 'a' * 64 if identifier == 'model-a:latest' else 'b' * 64},
        'properties': {'weight_size_bytes': 100, 'native_context': 131072},
        'capabilities': {'chat': True, 'tools': None, 'thinking': None,
                         'coding': None, 'vision': None, 'instruction_following': None},
        'metadata': {'checked_at': '2026-09-08T12:00:00Z',
                     'provenance': {'method': 'fixture_local_snapshot'}},
        'installed_match': {'installed': installed, 'installed_model_id': identifier if installed else None,
                            'match_status': match, 'catalog_digest': 'a' * 64 if identifier == 'model-a:latest' else 'b' * 64,
                            'installed_digest': 'a' * 64 if installed else None,
                            'revision_match': match == 'exact'},
    }


def catalog(*records):
    return {'status': 'ready', 'catalog_revision': 'fixture.1', 'record_count': len(records),
            'loader_errors': [], 'provenance_note': 'fixture provenance', 'models': list(records)}


def fit(identifier='model-a:latest', rating='GOOD', context=32768):
    return {'provider_available': True, 'models': [{
        'id': identifier, 'provider': 'ollama', 'capabilities': {'chat': True},
        'fit': {'rating': rating, 'confidence': 'medium', 'native_context': 131072,
                'recommended_context': context, 'estimated_headroom_bytes': 1024,
                'gpu_offload': 'full_estimated'},
        'observed_runtime': {'available': True, 'resident': False},
    }]}


def evidence_fact(catalog_id='model-a-latest', identifier='model-a:latest', revision='a' * 64,
                  evidence_id='fact-a', scope=None):
    return {
        'evidence_id': evidence_id, 'evidence_type': 'benchmark_result', 'claim_kind': 'FACT',
        'models': [{'catalog_id': catalog_id, 'provider': 'ollama', 'model_id': identifier,
                    'revision': revision, 'variant': None}],
        'profiles': ['GENERAL', 'CODING'],
        'source': {'reference': 'fixture://' + evidence_id, 'version': 'fixture-v1',
                   'observed_at': '2026-09-08T12:00:00Z', 'published_at': None},
        'trust_class': 'REPRODUCIBLE_BENCHMARK', 'confidence': 'MEDIUM',
        'provenance': {'method': 'fixture_measurement', 'references': [], 'corroborated': True},
        'scope': scope or {'workload': integration.WORKLOAD,
                           'conditions': integration.ENVIRONMENT, 'comparison_group': 'fixture-v1'},
        'facts': None,
        'measurement': {'benchmark': 'fixture-suite', 'version': 'v1', 'metric': 'pass_rate',
                        'value': 1, 'direction': 'higher_is_better', 'unit': 'percent'},
        'comparison': None, 'notes': None,
    }


def evidence_store(*rows):
    return evidence.validate_evidence({'schema_version': 1, 'evidence_revision': 'fixture-v1',
                                       'records': list(rows)})


class Wire:
    def __init__(self, request): self.request = io.BytesIO(request); self.response = bytearray()
    def makefile(self, *_args, **_kwargs): return self.request
    def sendall(self, data): self.response.extend(data)


def response(wire):
    headers, body = bytes(wire.response).split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body)


class NormalizationTests(unittest.TestCase):
    def build(self, records=None, payload=None, query=''):
        return integration.build_recommendations(records or catalog(record('model-a:latest')),
                                                 payload or fit(), integration.parse_request(query))

    def test_A_default_general_is_valid_explicit_abstention(self):
        result = self.build()
        self.assertEqual(result['profile'], 'GENERAL')
        self.assertEqual(result['scope'], 'installed')
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertEqual(result['confidence'], 'INSUFFICIENT')
        self.assertIn('TASK_EVIDENCE_INSUFFICIENT', result['excluded'][0]['reasons'])

    def test_B_coding_profile_is_accepted_without_inventing_coding_quality(self):
        result = self.build(query='profile=coding')
        self.assertEqual(result['profile'], 'CODING')
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertIsNone(result['candidates'][0]['inputs']['capabilities']['coding'])

    def test_D_and_E_scope_keeps_noninstalled_state_distinct(self):
        snapshots = catalog(record('model-a:latest'), record('model-b:latest', 'not_installed', False))
        installed = self.build(snapshots)
        known = self.build(snapshots, query='scope=known')
        installed_row = next(r for r in installed['candidates'] if r['catalog_id'] == 'model-b-latest')
        known_row = next(r for r in known['candidates'] if r['catalog_id'] == 'model-b-latest')
        self.assertIn('NOT_INSTALLED', installed_row['reasons'])
        self.assertIs(known_row['installed'], False)
        self.assertIs(known_row['requires_installation'], True)
        self.assertEqual(known_row['acquisition_feasibility'], 'UNKNOWN')

    def test_F_unknown_fit_stays_unknown_and_conservative(self):
        result = self.build(payload=fit(rating='UNKNOWN', context=None))
        row = result['candidates'][0]
        self.assertEqual(row['fit_state'], 'UNKNOWN')
        self.assertEqual(row['context_source'], 'UNKNOWN')
        self.assertIn('FIT_UNRESOLVED', row['reasons'])
        self.assertFalse(row['selectable'])

    def test_G_H_tight_needs_opt_in_and_never_adds_quality_evidence(self):
        default = self.build(payload=fit(rating='TIGHT', context=8192))
        allowed = self.build(payload=fit(rating='TIGHT', context=8192), query='allow_tight=true')
        self.assertEqual(default['candidates'][0]['eligibility'], 'EXCLUDED')
        self.assertIn('TIGHT_OPT_IN_REQUIRED', default['candidates'][0]['reasons'])
        self.assertEqual(allowed['candidates'][0]['eligibility'], 'CONDITIONAL')
        self.assertNotIn('TIGHT_OPT_IN_REQUIRED', allowed['candidates'][0]['reasons'])
        self.assertEqual(allowed['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_I_match_and_provenance_preserved(self):
        result = self.build()
        row = result['candidates'][0]
        self.assertEqual(row['catalog_match']['match_status'], 'exact')
        self.assertEqual(row['catalog_provenance']['source'], 'local_registry_snapshot')
        self.assertEqual(row['catalog_provenance']['method'], 'fixture_local_snapshot')

    def test_J_K_no_fake_ranking_and_abstention_passthrough(self):
        result = self.build(catalog(record('model-a:latest'), record('model-b:latest', 'not_installed', False)))
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertEqual(result['tiers']['recommended']['candidates'], [])
        self.assertEqual(result['tiers']['recommended']['abstention_reason'], 'NO_SUPPORTED_CANDIDATE')

    def test_required_context_is_a_hard_exact_requirement(self):
        result = self.build(query='required_context=65536')
        self.assertEqual((result['minimum_context'], result['target_context']), (65536, 65536))
        self.assertIn('BELOW_MINIMUM_CONTEXT', result['excluded'][0]['reasons'])

    def test_catalog_and_fit_fail_safely(self):
        unavailable = self.build({'status': 'unavailable', 'catalog_revision': None, 'record_count': 0}, fit())
        self.assertEqual(unavailable['status'], 'RECOMMENDATION_UNAVAILABLE')
        self.assertIn('CATALOG_UNAVAILABLE', unavailable['errors'])
        result = self.build(payload={'provider_available': False, 'error': 'provider_unavailable', 'models': []})
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertEqual(result['fit_snapshot']['error'], 'provider_unavailable')

    def test_L_normalizer_has_no_io_or_mutation(self):
        snapshots, payload = catalog(record('model-a:latest')), fit()
        before = deepcopy((snapshots, payload))
        with ExitStack() as stack:
            for name in ('builtins.open', 'socket.socket', 'socket.create_connection',
                         'urllib.request.urlopen', 'subprocess.Popen', 'os.system'):
                stack.enter_context(patch(name, side_effect=AssertionError('I/O prohibited')))
            result = self.build(snapshots, payload)
        self.assertEqual((snapshots, payload), before)
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_evidence_facts_are_visible_but_never_promoted_to_quality(self):
        store = evidence_store(evidence_fact())
        result = integration.build_recommendations(catalog(record('model-a:latest')), fit(),
                                                   integration.parse_request(''), store)
        row = result['candidates'][0]
        self.assertEqual(result['evidence']['status'], 'ready')
        self.assertEqual(result['evidence']['policy_comparison_count'], 0)
        self.assertEqual(row['evidence'][0]['evidence_id'], 'fact-a')
        self.assertFalse(row['evidence'][0]['policy_usable'])
        self.assertIn('TASK_EVIDENCE_INSUFFICIENT', row['reasons'])
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_only_exact_scoped_valid_comparisons_reach_policy(self):
        left = evidence_fact()
        right = evidence_fact('model-b-latest', 'model-b:latest', 'b' * 64, 'fact-b')
        comparison = {
            'evidence_id': 'comparison-a-b', 'evidence_type': 'curated_comparison',
            'claim_kind': 'QUALITY_JUDGMENT',
            'models': [left['models'][0], right['models'][0]], 'profiles': ['GENERAL'],
            'source': {'reference': 'fixture://comparison', 'version': 'fixture-v1',
                       'observed_at': '2026-09-08T12:00:00Z', 'published_at': None},
            'trust_class': 'CURATED_NOVA', 'confidence': 'MEDIUM',
            'provenance': {'method': 'fixture_curator', 'references': [], 'corroborated': True},
            'scope': dict(left['scope']), 'facts': None, 'measurement': None,
            'comparison': {'dimension': 'TASK', 'outcome': 'LEFT',
                           'supporting_evidence_ids': ['fact-a', 'fact-b'],
                           'rationale': 'Fixture result only.'}, 'notes': None,
        }
        store = evidence_store(left, right, comparison)
        payload = fit()
        payload['models'].append({**fit('model-b:latest')['models'][0]})
        result = integration.build_recommendations(
            catalog(record('model-a:latest'), record('model-b:latest')), payload,
            integration.parse_request(''), store)
        self.assertEqual(store.status, 'ready')
        self.assertEqual(result['evidence']['policy_comparison_count'], 1)
        first = next(row for row in result['candidates'] if row['catalog_id'] == 'model-a-latest')
        self.assertTrue(next(item for item in first['evidence']
                             if item['evidence_id'] == 'comparison-a-b')['policy_usable'])
        # Even valid comparative evidence cannot replace missing task suitability.
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_mismatched_evidence_revision_is_not_transferred_by_tag(self):
        store = evidence_store(evidence_fact(revision='c' * 64))
        result = integration.build_recommendations(catalog(record('model-a:latest')), fit(),
                                                   integration.parse_request(''), store)
        self.assertEqual(result['candidates'][0]['evidence'], [])
        self.assertEqual(result['evidence']['policy_comparison_count'], 0)

    def test_explicit_loader_failure_is_diagnostic_only(self):
        unavailable = evidence.EvidenceStore('unavailable', None, (), ('fixture unavailable',))
        with patch.object(integration, 'load_evidence', return_value=unavailable):
            result = integration.load_and_build_recommendations(
                catalog(record('model-a:latest')), fit(), integration.parse_request(''))
        self.assertEqual(result['evidence']['status'], 'unavailable')
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertIn('Evidence is unavailable or invalid; catalog and fit results remain advisory without it.',
                      result['limitations'])

    def test_bad_query_is_rejected_safely(self):
        for query in ('profile=magic', 'scope=cloud', 'allow_tight=yes',
                      'required_context=0', 'required_context=12k',
                      'profile=GENERAL&profile=CODING', 'unknown=value'):
            with self.subTest(query=query), self.assertRaises(integration.RecommendationRequestError):
                integration.parse_request(query)


class ApiTests(unittest.TestCase):
    def request(self, path):
        wire = Wire(f'GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
        view = catalog(record('model-a:latest'))
        with patch.object(server, 'catalog_view', return_value=view), patch.object(server, 'local_model_fit', return_value=fit()):
            server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_endpoint_defaults_and_coding(self):
        code, result = self.request('/recommendations/models')
        self.assertEqual(code, 200)
        self.assertEqual((result['profile'], result['scope']), ('GENERAL', 'installed'))
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        code, result = self.request('/recommendations/models?profile=CODING')
        self.assertEqual(code, 200)
        self.assertEqual(result['profile'], 'CODING')

    def test_endpoint_rejects_invalid_profile_without_snapshot_reads(self):
        wire = Wire(b'GET /recommendations/models?profile=magic HTTP/1.0\r\nHost: localhost\r\n\r\n')
        with patch.object(server, 'catalog_view', side_effect=AssertionError('must not read catalog')), patch.object(server, 'local_model_fit', side_effect=AssertionError('must not read fit')):
            server.Handler(wire, ('127.0.0.1', 1), None)
        code, result = response(wire)
        self.assertEqual(code, 400)
        self.assertEqual(result['error'], 'invalid_recommendation_request')

    def test_existing_routes_stay_unchanged(self):
        for path in ('/models?x=1', '/catalog/models?refresh=true', '/unknown'):
            wire = Wire(f'GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
            server.Handler(wire, ('127.0.0.1', 1), None)
            code, _ = response(wire)
            self.assertEqual(code, 400 if '?' in path else 404)


if __name__ == '__main__':
    unittest.main()


def guide(catalog_id='model-b-latest', identifier='model-b:latest', revision='b' * 64,
          evidence_id='guide-b', profiles=('GENERAL',), **changes):
    row = {
        'evidence_id': evidence_id, 'evidence_type': 'published_recommendation', 'claim_kind': 'QUALITY_JUDGMENT',
        'models': [{'catalog_id': catalog_id, 'provider': 'ollama', 'model_id': identifier,
                    'revision': revision, 'variant': None}],
        'profiles': list(profiles),
        'source': {'reference': 'https://guide.example/best', 'version': None,
                   'observed_at': '2026-10-04T11:40:00Z', 'published_at': None},
        'trust_class': 'CURATED_NOVA', 'confidence': 'LOW',
        'provenance': {'method': 'curated_from_published_guide', 'references': [], 'corroborated': False},
        'scope': {'workload': None, 'conditions': None, 'comparison_group': None},
        'recommendation': {'publisher': 'guide.example', 'title': 'Best models', 'summary': 'Good for chat.'},
        'notes': None,
    }
    row.update(changes)
    return row


class GuideSuggestionTests(unittest.TestCase):
    """REC-01: attributed published-guide evidence for not-installed artifacts."""
    ABSENT_FIT = {'rating': 'EXCELLENT', 'confidence': 'medium', 'native_context': 131072,
                  'recommended_context': 32768, 'estimated_headroom_bytes': 4096, 'gpu_offload': 'full_estimated'}

    def build(self, store, absent=True, disk=None, profile='general'):
        from acquisition import DiskSnapshot
        absent_record = record('model-b:latest', match='not_installed', installed=False)
        request = integration.parse_request(f'profile={profile}&scope=known')
        return integration.build_recommendations(
            catalog(absent_record), {'provider_available': True, 'models': []}, request, store,
            absent_fits={'model-b-latest': dict(self.ABSENT_FIT)} if absent else None,
            disk=disk or DiskSnapshot(1_000_000_000_000, 500_000_000_000),
            observed_at='2026-10-04T12:00:00+00:00')

    def test_guide_backed_absent_model_is_selectable_low_with_attribution(self):
        result = self.build(evidence_store(guide()))
        [row] = result['candidates']
        self.assertTrue(row['selectable'])
        self.assertEqual(row['confidence'], 'LOW')
        self.assertEqual(row['acquisition_feasibility'], 'FEASIBLE')
        self.assertEqual(result['tiers']['recommended']['candidates'][0]['catalog_id'], 'model-b-latest')
        [summary] = row['evidence']
        self.assertTrue(summary['policy_usable'])
        self.assertEqual(summary['attribution']['publisher'], 'guide.example')
        self.assertEqual(summary['attribution']['url'], 'https://guide.example/best')
        self.assertTrue(any('published guides' in item for item in result['limitations']))

    def test_no_guide_or_wrong_profile_or_revision_means_no_suggestion(self):
        for store in (evidence_store(), evidence_store(guide(profiles=('CODING',))),
                      evidence_store(guide(revision='c' * 64))):
            [row] = self.build(store)['candidates']
            self.assertFalse(row['selectable'])
            self.assertIn('TASK_EVIDENCE_INSUFFICIENT', row['reasons'])

    def test_absent_model_without_revision_bound_fit_is_not_suggested(self):
        [row] = self.build(evidence_store(guide()), absent=False)['candidates']
        self.assertFalse(row['selectable'])

    def test_not_enough_disk_blocks_the_suggestion(self):
        from acquisition import DiskSnapshot
        [row] = self.build(evidence_store(guide()), disk=DiskSnapshot(1_000_000_000, 10))['candidates']
        self.assertFalse(row['selectable'])
        self.assertIn('STORAGE_BLOCKED', row['reasons'])

    def test_guide_evidence_rules_are_strict(self):
        bad = [guide(confidence='MEDIUM'), guide(trust_class='TRUSTED_UPSTREAM'),
               guide(source={'reference': 'not-a-url', 'version': None, 'observed_at': '2026-10-04T11:40:00Z', 'published_at': None}),
               guide(provenance={'method': 'x', 'references': [], 'corroborated': True}),
               guide(recommendation={'publisher': 'p', 'title': 't'})]
        for row in bad:
            self.assertEqual(evidence_store(row).status, 'invalid', row)
        self.assertEqual(evidence_store(guide()).status, 'ready')
