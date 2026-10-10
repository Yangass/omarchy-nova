"""P1-01B live normalization/API tests using isolated read-only fixtures."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from acquisition import DiskSnapshot
import onboarding_integration as integration
from providers import ProviderUnavailable
import server

GIB = 1024 ** 3


def profile(available=True):
    return {
        'schema_version': 1,
        'capacity': {
            'cpu': {'model': 'Fixture CPU', 'logical_cores': 8, 'physical_cores': 4},
            'memory': {'total_bytes': 64 * GIB}, 'gpus': [],
        },
        'dynamic': {'memory': {'available_bytes': 48 * GIB}, 'gpus': [],
                    'storage': {'total_bytes': 256 * GIB, 'free_bytes': 128 * GIB}},
        'local_ai': {'provider': 'ollama', 'available': available},
    }


def model(identifier='model-a:latest'):
    return {
        'id': identifier, 'provider': 'ollama', 'display_name': identifier,
        'local': True, 'installed': True, 'available': True,
        'capabilities': {'chat': True},
        'metadata': {
            'size_bytes': 2 * GIB, 'context_length': 32768,
            'architecture': 'qwen3',
            'attention': {'layers': 16, 'kv_heads': 4,
                          'key_length': 64, 'value_length': 64},
        },
    }


def catalog_record(identifier='model-a:latest', match='exact', installed=True):
    digest = 'a' * 64
    return {
        'catalog_id': identifier.replace(':', '-'),
        'display_name': 'Fixture model',
        'source': {'provider': 'ollama', 'upstream_id': identifier,
                   'revision_digest': digest},
        'properties': {'weight_size_bytes': 2 * GIB},
        'acquisition': {
            'provider': 'ollama', 'artifact_id': identifier,
            'exact_tag': identifier.rsplit(':', 1)[1],
            'expected_revision': digest, 'expected_size_bytes': 2 * GIB,
            'source': 'ollama_registry',
            'provenance': {'method': 'fixture', 'observed_at': '2026-09-01T00:00:00Z'},
        },
        'installed_match': {
            'installed': installed,
            'installed_model_id': identifier if installed else None,
            'match_status': match,
            'installed_digest': digest if installed else None,
        },
    }


def catalog(*records):
    return {'status': 'ready', 'catalog_revision': 'fixture.1',
            'models': list(records), 'loader_errors': []}


class FakeProvider:
    def __init__(self, models=None, running=None, unavailable=False):
        self.model_rows = [model()] if models is None else models
        self.running_rows = [{'name': 'model-a:latest'}] if running is None else running
        self.unavailable = unavailable
        self.calls = {'fit_models': 0, 'running_models': 0}

    def fit_models(self):
        self.calls['fit_models'] += 1
        if self.unavailable:
            raise ProviderUnavailable('offline fixture')
        return json.loads(json.dumps(self.model_rows))

    def running_models(self):
        self.calls['running_models'] += 1
        if self.unavailable:
            raise ProviderUnavailable('offline fixture')
        return json.loads(json.dumps(self.running_rows))

    def preload(self, *_args):
        raise AssertionError('onboarding read must not load a model')

    def unload(self, *_args):
        raise AssertionError('onboarding read must not unload a model')

    def stream_chat(self, *_args):
        raise AssertionError('onboarding read must not run inference')


class StableReader:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return json.loads(json.dumps(self.value)) if isinstance(self.value, dict) else self.value


class ChangingRuntime:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {'state': 'idle', 'updated_at': 'one'} if self.calls == 1 else {
            'state': 'model_switching', 'model': 'model-b:latest', 'updated_at': 'two'}


class StepClock:
    def __init__(self, step):
        self.value, self.step = -step, step

    def __call__(self):
        self.value += self.step
        return self.value


class Wire:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


def response(wire):
    headers, body = bytes(wire.response).split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body)


class OnboardingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = Path(self.temporary.name) / 'model.json'

    def tearDown(self):
        self.temporary.cleanup()

    def write_config(self, provider='ollama', selected='model-a:latest'):
        self.config.write_text(json.dumps({
            'active_provider': provider, 'active_model': selected,
        }))

    def collect(self, *, provider=None, runtime=None, records=None,
                catalog_snapshot=None, **changes):
        provider = provider or FakeProvider()
        effective = (None, None)
        if self.config.is_file():
            try:
                configured = json.loads(self.config.read_text())
                effective = (configured.get('active_provider'), configured.get('active_model'))
            except (OSError, ValueError):
                pass
        values = dict(
            config_path=self.config,
            providers={'ollama': provider},
            default_provider='ollama',
            effective_selection_reader=StableReader(effective),
            runtime_reader=runtime or StableReader({'state': 'idle', 'updated_at': 'same'}),
            acquisition_reader=StableReader([] if records is None else records),
            clock=StepClock(0),
            profile_collector=lambda available: profile(available),
            catalog_collector=lambda include_models=True: catalog_snapshot or
                catalog(catalog_record()),
            recommendation_loader=lambda *_args: {
                'status': 'NO_DEFENSIBLE_RECOMMENDATION', 'confidence': 'INSUFFICIENT',
                'tiers': {'recommended': {'candidates': []}},
            },
            storage_collector=lambda: (DiskSnapshot(256 * GIB, 128 * GIB), None),
            readiness_collector=lambda: True,
            saved_fit_loader=lambda *_args: {'rating': 'GOOD'},
        )
        values.update(changes)
        return integration.collect_onboarding_state(**values), provider

    def test_configuration_states_are_distinct_and_do_not_use_fallback(self):
        missing, _ = self.collect()
        self.assertEqual(missing['configuration']['state'], 'MISSING')
        self.assertFalse(missing['configuration']['explicit_selection'])
        self.assertEqual(missing['primary_state'], 'NEEDS_ACTIVATION')
        self.assertNotEqual(missing['target_id'], 'qwen3.5:35b')

        self.config.write_text('{')
        malformed, _ = self.collect()
        self.assertEqual(malformed['configuration']['state'], 'MALFORMED')
        self.assertIn('MODEL_CONFIGURATION_MALFORMED', malformed['explanation_codes'])

        self.write_config('unsupported-provider')
        unsupported, _ = self.collect()
        self.assertEqual(unsupported['configuration']['state'], 'UNSUPPORTED')
        self.assertIn('MODEL_CONFIGURATION_UNSUPPORTED', unsupported['explanation_codes'])

        self.write_config()
        valid, _ = self.collect()
        self.assertEqual(valid['configuration']['state'], 'VALID')
        self.assertTrue(valid['configuration']['explicit_selection'])
        self.assertEqual(valid['configuration']['model'], 'model-a:latest')

    def test_unreadable_or_nonregular_configuration_is_unknown(self):
        self.config.mkdir()
        observed = integration.inspect_explicit_configuration(self.config, {'ollama'})
        self.assertEqual(observed.state, 'UNKNOWN')

    def test_provider_unavailability_is_preserved(self):
        self.write_config()
        result, provider = self.collect(provider=FakeProvider(unavailable=True))
        self.assertEqual(result['observations']['provider']['state'], 'UNAVAILABLE')
        self.assertEqual(result['primary_state'], 'NEEDS_USER_SETUP')
        self.assertEqual(provider.calls, {'fit_models': 1, 'running_models': 0})

    def test_ollama_below_minimum_version_needs_an_update(self):
        self.write_config()
        cases = {(0, 6, 4): 'OUTDATED', (0, 6, 5): 'AVAILABLE', (0, 33, 3): 'AVAILABLE', None: 'AVAILABLE'}
        for version, state in cases.items():
            provider = FakeProvider()
            provider.version = lambda version=version: version
            result, _ = self.collect(provider=provider)
            observed = result['observations']['provider']
            self.assertEqual(observed['state'], state, version)
            self.assertEqual(observed['version'], '.'.join(map(str, version)) if version else None)
            self.assertEqual(observed['minimum_version'], '0.6.5')
            if state == 'OUTDATED':
                self.assertEqual(result['primary_state'], 'NEEDS_USER_SETUP')
                self.assertIn('PROVIDER_OUTDATED', result['explanation_codes'])
                self.assertNotIn('SEND_FIRST_MESSAGE', result['allowed_actions'])
            else:
                self.assertNotIn('PROVIDER_OUTDATED', result['explanation_codes'])

    def test_inventory_mismatch_requires_an_explicit_other_activation(self):
        self.write_config(selected='model-b:latest')
        result, _ = self.collect()
        self.assertEqual(result['primary_state'], 'NEEDS_ACTIVATION')
        self.assertIn('SELECTED_MODEL_NOT_INSTALLED', result['explanation_codes'])
        self.assertIn('CHOOSE_INSTALLED_MODEL', result['allowed_actions'])

    def test_active_acquisition_precedes_coherent_ready_selection(self):
        self.write_config()
        row = {'state': 'DOWNLOADING',
               'preflight': {'descriptor': {'catalog_id': 'model-b-latest'}}}
        result, _ = self.collect(records=[row])
        self.assertEqual(result['primary_state'], 'ACQUISITION_IN_PROGRESS')
        self.assertEqual(result['target_id'], 'model-b-latest')
        self.assertNotIn('SEND_FIRST_MESSAGE', result['allowed_actions'])

    def test_runtime_model_transition_maps_to_activation(self):
        self.write_config()
        runtime = StableReader({'state': 'model_switching', 'model': 'model-b:latest',
                                'updated_at': 'same'})
        result, _ = self.collect(runtime=runtime)
        self.assertEqual(result['primary_state'], 'ACTIVATING')
        self.assertEqual(result['target_id'], 'model-b:latest')

    def test_coherent_returning_user_is_ready_and_collectors_run_once(self):
        self.write_config()
        counts = {'profile': 0, 'catalog': 0, 'recommendation': 0,
                  'storage': 0, 'readiness': 0}
        def counted(name, value):
            def call(*_args, **_kwargs):
                counts[name] += 1
                return value
            return call
        provider = FakeProvider()
        result, _ = self.collect(
            provider=provider,
            profile_collector=counted('profile', profile()),
            catalog_collector=counted('catalog', catalog(catalog_record())),
            recommendation_loader=counted('recommendation', {
                'status': 'NO_DEFENSIBLE_RECOMMENDATION', 'confidence': 'INSUFFICIENT',
                'tiers': {'recommended': {'candidates': []}},
            }),
            storage_collector=counted('storage', (DiskSnapshot(256 * GIB, 128 * GIB), None)),
            readiness_collector=counted('readiness', True),
        )
        self.assertEqual(result['primary_state'], 'READY')
        self.assertEqual(result['allowed_actions'], ['SEND_FIRST_MESSAGE'])
        self.assertEqual(provider.calls, {'fit_models': 1, 'running_models': 1})
        self.assertEqual(counts, {key: 1 for key in counts})

    def test_changed_runtime_and_duplicate_inventory_are_conflicts(self):
        self.write_config()
        stale, _ = self.collect(runtime=ChangingRuntime())
        self.assertEqual(stale['primary_state'], 'CHECKING')
        self.assertEqual(stale['collection']['status'], 'STALE')
        duplicate_provider = FakeProvider(models=[model(), model()])
        conflict, _ = self.collect(provider=duplicate_provider)
        self.assertEqual(conflict['primary_state'], 'CHECKING')
        self.assertEqual(conflict['collection']['status'], 'CONFLICTING')

    def test_collection_budget_stops_optional_probes_and_marks_stale(self):
        self.write_config()
        called = {'profile': 0}
        def should_not_run(_available):
            called['profile'] += 1
            return profile()
        result, provider = self.collect(clock=StepClock(10), max_seconds=5,
                                        profile_collector=should_not_run)
        self.assertEqual(result['primary_state'], 'CHECKING')
        self.assertIn('collection_budget_exceeded', result['collection']['reasons'])
        self.assertEqual(called['profile'], 0)
        self.assertEqual(provider.calls['fit_models'], 1)

    def test_unknown_hardware_is_a_visible_limit_for_coherent_ready_runtime(self):
        self.write_config()
        result, _ = self.collect(profile_collector=lambda _available: None)
        self.assertEqual(result['primary_state'], 'READY')
        self.assertIn('HARDWARE_PROFILE_UNKNOWN', result['limitations'])
        self.assertIn('SEND_FIRST_MESSAGE', result['allowed_actions'])

    def test_residency_is_not_ready_when_explicit_and_effective_selection_conflict(self):
        self.write_config()
        result, _ = self.collect(
            effective_selection_reader=StableReader(('ollama', 'model-b:latest')),
            profile_collector=lambda _available: None,
        )
        self.assertEqual(result['primary_state'], 'CHECKING')
        self.assertEqual(result['collection']['status'], 'CONFLICTING')
        self.assertIn('explicit_and_effective_selection_disagree',
                      result['collection']['reasons'])
        self.assertNotIn('SEND_FIRST_MESSAGE', result['allowed_actions'])

    def test_reading_snapshot_does_not_mutate_config_provider_or_runtime(self):
        self.write_config()
        before = self.config.read_bytes()
        provider = FakeProvider()
        with ExitStack() as stack:
            stack.enter_context(patch('urllib.request.urlopen',
                                      side_effect=AssertionError('network not injected')))
            stack.enter_context(patch('subprocess.Popen',
                                      side_effect=AssertionError('process mutation forbidden')))
            stack.enter_context(patch('os.system',
                                      side_effect=AssertionError('shell mutation forbidden')))
            stack.enter_context(patch('os.replace',
                                      side_effect=AssertionError('filesystem mutation forbidden')))
            result, _ = self.collect(provider=provider)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn(result['primary_state'], ('READY', 'NEEDS_ACTIVATION'))
        self.assertEqual(provider.calls, {'fit_models': 1, 'running_models': 1})

    def test_absent_eligible_candidate_uses_existing_preflight_policy(self):
        missing_record = catalog_record('model-b:latest', 'not_installed', False)
        provider = FakeProvider(models=[], running=[])
        result, _ = self.collect(provider=provider,
                                 catalog_snapshot=catalog(missing_record))
        self.assertEqual(result['primary_state'], 'NEEDS_MODEL')
        self.assertEqual(result['allowed_actions'], ['PROPOSE_INSTALL'])
        self.assertEqual(result['candidate_catalog_ids'], ['model-b-latest'])


class OnboardingApiTests(unittest.TestCase):
    def request(self, path='/onboarding/state', method='GET'):
        wire = Wire(f'{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
        payload = {'schema_version': 1, 'primary_state': 'READY',
                   'allowed_actions': ['SEND_FIRST_MESSAGE']}
        with patch.object(server, 'local_onboarding_state', return_value=payload):
            server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_read_only_endpoint_returns_classifier_result(self):
        status, payload = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(payload['primary_state'], 'READY')

    def test_endpoint_rejects_query_and_mutation(self):
        self.assertEqual(self.request('/onboarding/state?refresh=true')[0], 400)
        self.assertEqual(self.request(method='POST')[0], 404)


if __name__ == '__main__':
    unittest.main()
