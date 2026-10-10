"""v0.12E protocol/authority tests; no real pull requests are sent."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ollama_acquisition as adapter
from acquisition_lifecycle import AcquisitionLifecycle, LifecycleError, frozen_descriptor
from test_acquisition_lifecycle import fixture
from acquisition_fit import saved_fit
import ollama_readiness
import server


class RealAdapterTests(unittest.TestCase):
    def setUp(self):
        self.preflight = fixture()
        self.descriptor = frozen_descriptor(self.preflight['descriptor'])
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'

    def stream(self, events, status=200):
        stream = io.BytesIO(b''.join(json.dumps(e).encode() + b'\n' for e in events))
        stream.status = status
        conn = Mock(); conn.getresponse.return_value = stream
        return conn

    def pull(self, events, existing='absent', status=200):
        job = adapter.OllamaAcquisitionAdapter(); conn = self.stream(events, status)
        with patch.object(adapter, 'connection', return_value=conn), patch.object(adapter, 'inspect_artifact', return_value={'state': existing}), patch.object(adapter, 'write_readiness', return_value=True):
            job._pull(self.descriptor)
        return job, conn

    def test_A_fixed_target_and_api(self):
        job, conn = self.pull([{'status':'success'}])
        args = conn.request.call_args.args
        self.assertEqual(args[:2], ('POST', '/api/pull'))
        self.assertEqual(json.loads(args[2]), {'model':'registry.ollama.ai/library/fixture:one', 'stream':True, 'insecure':False})
        self.assertEqual(job.observe_progress()['state'], 'complete')
        conn.close.assert_called_once()

    def test_B_arbitrary_identity_and_tag_rejected(self):
        for name in ('https://evil/x:one', 'evil/fixture:one', '../x:one', 'fixture:cloud', 'fixture:one?x=1'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                adapter.validate_target(replace(self.descriptor, artifact_id=name))
        with self.assertRaises(ValueError): adapter.validate_target(replace(self.descriptor, exact_tag='override'))
        with self.assertRaises(ValueError): adapter.validate_target(replace(self.descriptor, source_trust_state='UNVERIFIED'))

    def test_C_unavailable(self):
        job = adapter.OllamaAcquisitionAdapter()
        with patch.object(adapter, 'write_readiness', return_value=True), patch.object(adapter, 'inspect_artifact', return_value={'state':'absent'}), patch.object(adapter, 'connection', side_effect=OSError):
            job._pull(self.descriptor)
        self.assertEqual(job.observe_progress()['state'], 'failed')

    def test_D_readiness_unknown_no_pull(self):
        job = adapter.OllamaAcquisitionAdapter()
        with patch.object(adapter, 'write_readiness', return_value=None), patch.object(adapter, 'connection') as connect:
            job._pull(self.descriptor)
        connect.assert_not_called()
        self.assertEqual(job.observe_progress()['state'], 'failed')

    def test_E_real_lifecycle_verified_success(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']
        store.decide(identifier, 'approved', lambda _: self.preflight)
        with patch.object(adapter, 'Thread'):
            store.advance(identifier)
        job = store.jobs[identifier]; job._publish('complete')
        self.assertEqual(store.advance(identifier)['state'], 'VERIFYING')
        with patch.object(adapter, 'inspect_artifact', return_value={'state':'exact','revision':self.descriptor.expected_revision,'footprint_bytes':self.descriptor.expected_artifact_size}):
            result = store.advance(identifier)
        self.assertEqual(result['state'], 'INSTALLED'); self.assertFalse(result['simulation'])
        self.assertEqual(result['progress']['verified_footprint_bytes'], self.descriptor.expected_artifact_size)
        self.assertEqual(result['activation'], 'not_performed')

    def test_F_provider_success_is_not_verification(self):
        job, _ = self.pull([{'status':'success'}])
        with patch.object(adapter, 'inspect_artifact', return_value={'state':'mismatch'}):
            self.assertIsNone(job.verify(self.descriptor))

    def test_G_H_duplicate_and_replay(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']
        with self.assertRaises(LifecycleError): store.propose(self.preflight)
        store.decide(identifier, 'approved', lambda _: self.preflight)
        with self.assertRaises(LifecycleError): store.decide(identifier, 'approved', lambda _: self.preflight)

    def test_I_J_K_recovery_exact_absent_unknown_mismatch(self):
        for observed, expected in [('exact','INSTALLED'), ('absent','RECOVERY_REQUIRED'), ('unknown','RECOVERY_REQUIRED'), ('mismatch','FAILED')]:
            path = self.path.with_name(observed + '.json')
            store = AcquisitionLifecycle(path, adapter_factory=adapter.OllamaAcquisitionAdapter)
            identifier = store.propose(self.preflight)['id']; store.decide(identifier, 'approved', lambda _: self.preflight)
            observation = {'state': observed, 'revision': self.descriptor.expected_revision, 'footprint_bytes': self.descriptor.expected_artifact_size}
            with patch.object(adapter, 'inspect_artifact', return_value=observation), patch.object(adapter.OllamaAcquisitionAdapter, 'begin', side_effect=AssertionError):
                recovered = AcquisitionLifecycle(path, adapter_factory=adapter.OllamaAcquisitionAdapter)
            self.assertEqual(recovered.get(identifier)['state'], expected)
            if expected == 'RECOVERY_REQUIRED':
                with self.assertRaises(LifecycleError): recovered.propose(self.preflight)

    def test_L_cancellation_truthful(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']
        with self.assertRaisesRegex(LifecycleError, 'cancel_unsupported'): store.cancel(identifier)
        self.assertEqual(store.get(identifier)['state'], 'PROPOSED')

    def test_M_exact_installed_no_pull(self):
        job, conn = self.pull([], existing='exact')
        conn.request.assert_not_called()
        self.assertEqual(job.observe_progress()['state'], 'complete')

    def test_N_O_no_activation_authority(self):
        before = (server.ACTIVE_PROVIDER, server.ACTIVE_MODEL)
        with patch.object(server, 'select_model', side_effect=AssertionError), patch.object(server, 'execute_action', side_effect=AssertionError):
            self.pull([{'status':'success'}])
        self.assertEqual(before, (server.ACTIVE_PROVIDER, server.ACTIVE_MODEL))

    def test_progress_is_not_network_bytes_or_global_percent(self):
        job, _ = self.pull([{'digest':'sha256:'+'b'*64,'total':100,'completed':100},
                            {'digest':'sha256:'+'c'*64,'total':200,'completed':50}, {'status':'success'}])
        result = job.observe_progress()
        self.assertIsNone(result['progress'])
        self.assertEqual(result['transfer'], {
            'bytes_transferred': None,
            'provider_completed_bytes': 150,
            'provider_total_bytes': 300,
            'current_blob_index': 2,
            'current_blob_completed_bytes': 50,
            'current_blob_total_bytes': 200,
        })

    def test_current_blob_progress_is_determinate_without_becoming_global(self):
        digest = 'sha256:' + 'b' * 64
        job, _ = self.pull([
            {'digest': digest, 'total': 200, 'completed': 0},
            {'digest': digest, 'total': 200, 'completed': 50},
            {'status': 'success'},
        ])
        transfer = job.observe_progress()['transfer']
        self.assertEqual(transfer['current_blob_index'], 1)
        self.assertEqual(transfer['current_blob_completed_bytes'], 50)
        self.assertEqual(transfer['current_blob_total_bytes'], 200)
        self.assertEqual(transfer['provider_completed_bytes'], 50)
        self.assertEqual(transfer['provider_total_bytes'], 200)
        self.assertIsNone(transfer['bytes_transferred'])

    def test_blob_transitions_are_indexed_and_repeated_events_are_replaced(self):
        first, second = 'sha256:' + 'b' * 64, 'sha256:' + 'c' * 64
        job, _ = self.pull([
            {'digest': first, 'total': 100, 'completed': 100},
            {'digest': first, 'total': 100, 'completed': 100},
            {'digest': second, 'total': 200, 'completed': 0},
            {'status': 'success'},
        ])
        transfer = job.observe_progress()['transfer']
        self.assertEqual(transfer['current_blob_index'], 2)
        self.assertEqual(transfer['current_blob_completed_bytes'], 0)
        self.assertEqual(transfer['provider_completed_bytes'], 100)
        self.assertEqual(transfer['provider_total_bytes'], 300)

    def test_missing_blob_total_stays_indeterminate(self):
        digest = 'sha256:' + 'b' * 64
        job, _ = self.pull([{'digest': digest, 'status': 'pulling manifest'},
                            {'status': 'success'}])
        transfer = job.observe_progress()['transfer']
        self.assertEqual(transfer['current_blob_index'], 1)
        self.assertIsNone(transfer['current_blob_completed_bytes'])
        self.assertIsNone(transfer['current_blob_total_bytes'])

    def test_truncated_and_redirect_response_fail(self):
        for events, status in [([], 200), ([{'error':'private provider output'}],200), ([{'status':'success'}],302)]:
            job, _ = self.pull(events, status=status)
            self.assertEqual(job.observe_progress()['state'], 'failed')
            self.assertNotIn('private', json.dumps(job.observe_progress()))

    def test_failure_retains_last_blob_observation_and_sanitized_detail(self):
        digest = 'sha256:' + 'b' * 64
        job, _ = self.pull([
            {'digest': digest, 'total': 100, 'completed': 24},
            {'error': 'private token=abc https://example.test /tmp/provider'},
        ])
        observed = job.observe_progress()
        self.assertEqual(observed['state'], 'failed')
        self.assertEqual(observed['transfer']['current_blob_completed_bytes'], 24)
        self.assertEqual(observed['failure_detail']['code'], 'provider_reported_error')
        self.assertEqual(observed['failure_detail']['provider_activity'], 'INACTIVE_CONFIRMED')
        self.assertNotIn('abc', json.dumps(observed))
        self.assertNotIn('example.test', json.dumps(observed))
        self.assertNotIn('/tmp', json.dumps(observed))

    def test_mismatched_existing_artifact_never_overwritten(self):
        job, conn = self.pull([], existing='mismatch')
        conn.request.assert_not_called()
        self.assertEqual(job.observe_progress()['state'], 'failed')

    def fake_proc(self, root, pid, name='ollama', uid=960, cgroup='0::/system.slice/ollama.service', start=100):
        d = root / pid; d.mkdir()
        (d/'status').write_text(f'Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        (d/'cgroup').write_text(cgroup + '\n')
        (d/'stat').write_text(f'{pid} ({name}) S ' + ' '.join(['0'] * 18) + f' {start} 0\n')

    def test_readiness_accepts_server_with_model_runners_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.fake_proc(root, '10', start=100); self.fake_proc(root, '11', start=500)
            self.assertEqual(ollama_readiness.service_process_starts(['10', '11'], 960, root), [100, 500])
            self.fake_proc(root, '12', name='bash')
            self.assertIsNone(ollama_readiness.service_process_starts(['10', '12'], 960, root))
            self.fake_proc(root, '13', uid=1000)
            self.assertIsNone(ollama_readiness.service_process_starts(['10', '13'], 960, root))
            self.fake_proc(root, '14', cgroup='0::/user.slice')
            self.assertIsNone(ollama_readiness.service_process_starts(['10', '14'], 960, root))
            self.assertIsNone(ollama_readiness.service_process_starts([], 960, root))

    def test_readiness_unsupported_storage_is_unknown(self):
        with patch.object(ollama_readiness, 'OLLAMA_STORAGE', Path('/other')):
            self.assertIsNone(ollama_readiness.write_readiness())

    def test_recovered_simulation_never_runs_real(self):
        simulated = AcquisitionLifecycle(self.path)
        identifier = simulated.propose(self.preflight)['id']; simulated.decide(identifier, 'approved', lambda _: self.preflight)
        with patch.object(adapter, 'inspect_artifact', side_effect=AssertionError):
            recovered = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        self.assertEqual(recovered.get(identifier)['state'], 'RECOVERY_REQUIRED')

    def test_revision_bound_fit_survives_absence(self):
        root = Path(__file__).resolve().parent.parent
        catalog = json.loads((root/'catalog/models.json').read_text())
        record = next(r for r in catalog['models'] if r['catalog_id'] == 'qwen3-8b-q4-k-m')
        snapshot = {'provider_available': True, 'budget':{'operating_budget_bytes':24*1024**3,'mode':'gpu_candidate','assumption':'fixture'}}
        result = saved_fit(record, snapshot)
        self.assertEqual(result['rating'], 'EXCELLENT')
        record['source']['revision_digest'] = 'wrong'
        self.assertIsNone(saved_fit(record, snapshot))

    def test_real_failed_stream_requires_reconciliation(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']; store.decide(identifier,'approved',lambda _:self.preflight)
        with patch.object(adapter, 'Thread'): store.advance(identifier)
        store.jobs[identifier]._publish('failed')
        self.assertEqual(store.advance(identifier)['state'], 'RECOVERY_REQUIRED')

    def test_real_lifecycle_mismatch_is_failed(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']; store.decide(identifier,'approved',lambda _:self.preflight)
        with patch.object(adapter, 'Thread'): store.advance(identifier)
        store.jobs[identifier]._publish('complete'); store.advance(identifier)
        with patch.object(adapter, 'inspect_artifact', return_value={'state':'mismatch'}):
            result = store.advance(identifier)
        self.assertEqual((result['state'],result['verification']), ('FAILED','mismatch'))

    def test_inspection_absence_and_ambiguity(self):
        with patch.object(adapter, 'local_json', return_value={'models':[]}), patch.object(adapter, 'local_inventory', return_value={'records':[],'status':'ready'}):
            self.assertEqual(adapter.inspect_artifact(self.descriptor)['state'],'absent')
        with patch.object(adapter, 'local_json', return_value={'models':[{'name':'fixture:one'}]*2}), patch.object(adapter, 'local_inventory', return_value={'records':[],'status':'ready'}):
            self.assertEqual(adapter.inspect_artifact(self.descriptor)['state'],'unknown')

    def test_private_progress_shape_survives_restart(self):
        store = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        identifier = store.propose(self.preflight)['id']; store.decide(identifier,'approved',lambda _:self.preflight)
        with patch.object(adapter, 'Thread'): store.advance(identifier)
        store.jobs[identifier]._publish('downloading', {'bytes_transferred':None,'provider_total_bytes':200,'provider_completed_bytes':50})
        store.advance(identifier)
        with patch.object(adapter, 'inspect_artifact', return_value={'state':'unknown'}): recovered = AcquisitionLifecycle(self.path, adapter_factory=adapter.OllamaAcquisitionAdapter)
        self.assertIsNone(recovered.get(identifier)['progress'])
        self.assertEqual(recovered.get(identifier)['historical_progress']['progress']['provider_completed_bytes'],50)

if __name__ == '__main__': unittest.main()
