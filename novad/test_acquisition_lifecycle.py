"""v0.12D vectors: all execution is a simulated job in a temporary state root."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from acquisition import (AcquisitionDescriptor, DiskSnapshot, FitSnapshot, InventorySnapshot,
                         ProviderPreflight, PreflightRequest, evaluate_preflight, GIB)
from acquisition_lifecycle import AcquisitionLifecycle, LifecycleError, SimulatedAdapter, MAX_RECORDS
from test_acquisition_integration import Wire, response
import server


def fixture():
    return evaluate_preflight(AcquisitionDescriptor('fixture', 'r1', 'ollama', 'TRUSTED',
        'fixture:one', 'one', 'a' * 64, GIB, ('local-test',)), InventorySnapshot('NOT_INSTALLED'),
        FitSnapshot('GOOD'), DiskSnapshot(100 * GIB, 50 * GIB), ProviderPreflight(True, True, True),
        PreflightRequest('r1'))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'state.json'
        self.now = 1000
        self.store = AcquisitionLifecycle(self.path, clock=lambda: self.now)
        self.preflight = fixture()

    def propose(self): return self.store.propose(self.preflight)['id']
    def approve(self, identifier): return self.store.decide(identifier, 'approved', lambda _: self.preflight)
    def execute(self, scenario='success'):
        self.store.adapter_factory = lambda: SimulatedAdapter(scenario)
        identifier = self.propose(); self.approve(identifier); self.store.run(identifier)
        return identifier

    def test_A_immutable_snapshot_and_private_durable_file(self):
        identifier = self.propose()
        self.preflight['descriptor']['artifact_id'] = 'changed'
        row = self.store.get(identifier); row['preflight']['descriptor']['artifact_id'] = 'also-changed'
        self.assertEqual(self.store.get(identifier)['preflight']['descriptor']['artifact_id'], 'fixture:one')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(self.path.read_text())['records'][identifier]['state'], 'PROPOSED')

    def test_B_C_blocked_and_no_op_create_no_state(self):
        for status in ('BLOCKED', 'NO_OP_ALREADY_INSTALLED'):
            data = fixture(); data.update(eligible=False, status=status)
            with self.assertRaises(LifecycleError): self.store.propose(data)
        self.assertFalse(self.path.exists())

    def request(self, path, body=b'', method='POST'):
        wire = Wire(f'{method} {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
        with patch.object(server, 'ACQUISITIONS', self.store), patch.object(server, 'local_acquisition_preflight', return_value=self.preflight):
            server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_D_overrides_rejected_before_preflight(self):
        for path, body in (('/acquisition/models/fixture/propose?revision=x', b''),
                           ('/acquisition/models/fixture/propose', b'{"size":1}')):
            self.assertEqual(self.request(path, body)[0], 400)
        self.assertFalse(self.store.records)

    def test_E_existing_approval_dispatch_simulates(self):
        code, row = self.request('/acquisition/models/fixture/propose')
        self.assertEqual(code, 201)
        with patch.object(server, 'ACQUISITIONS', self.store), patch.object(server, 'local_acquisition_preflight', return_value=self.preflight), patch.object(server, 'Thread') as thread:
            code, approved = server.APPROVALS.decide(row['id'], 'approved')
        self.assertEqual((code, approved['state']), (202, 'APPROVED'))
        thread.assert_called_once(); thread.return_value.start.assert_called_once()
        self.store.run(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'], 'INSTALLED')

    def test_F_denial_no_adapter(self):
        identifier = self.propose()
        with patch.object(self.store, 'adapter_factory', side_effect=AssertionError):
            row = self.store.decide(identifier, 'denied', lambda _: self.preflight)
        self.assertEqual(row['state'], 'REJECTED')

    def test_G_approval_replay(self):
        identifier = self.propose(); self.approve(identifier)
        with self.assertRaisesRegex(LifecycleError, 'approval_already_used'): self.approve(identifier)

    def test_H_expiry(self):
        identifier = self.propose(); self.now += 120
        with self.assertRaisesRegex(LifecycleError, 'expired_approval'): self.approve(identifier)
        self.assertEqual(self.store.get(identifier)['state'], 'EXPIRED')

    def test_I_success_requires_separate_verification(self):
        identifier = self.propose(); self.approve(identifier)
        self.store.advance(identifier); self.store.advance(identifier); self.store.advance(identifier)
        self.assertEqual(self.store.get(identifier)['state'], 'VERIFYING')
        row = self.store.advance(identifier)
        self.assertEqual((row['state'], row['verification'], row['simulation']), ('INSTALLED', 'matched', True))

    def test_J_mismatch(self):
        row = self.store.get(self.execute('mismatch'))
        self.assertEqual((row['state'], row['error']), ('FAILED', 'revision_mismatch'))

    def test_K_provider_failure(self):
        self.assertEqual(self.store.get(self.execute('failure'))['state'], 'FAILED')

    def test_L_cancel_confirmed_only_on_observation(self):
        identifier = self.propose(); self.approve(identifier); self.store.advance(identifier)
        self.assertEqual(self.store.cancel(identifier)['state'], 'CANCEL_REQUESTED')
        self.assertEqual(self.store.advance(identifier)['state'], 'CANCELLED')

    def test_M_duplicate_conflict(self):
        self.propose()
        with self.assertRaisesRegex(LifecycleError, 'acquisition_conflict'): self.propose()

    def test_N_terminal_restart_and_no_rerun(self):
        identifier = self.execute()
        before = self.store.get(identifier)
        restarted = AcquisitionLifecycle(self.path)
        self.assertEqual(restarted.get(identifier)['state'], 'INSTALLED')
        with self.assertRaises(LifecycleError): restarted.advance(identifier)
        fresh = restarted.propose(self.preflight)
        self.assertNotEqual(fresh['id'], identifier)
        self.assertEqual(fresh['state'], 'PROPOSED')
        self.assertEqual(restarted.get(identifier), before)

    def test_reacquire_refused_when_absence_not_confirmed(self):
        identifier = self.execute()
        blocked = dict(self.preflight, inventory_state='UNKNOWN')
        with self.assertRaisesRegex(LifecycleError, 'acquisition_already_completed'):
            self.store.propose(blocked)
        self.assertEqual(self.store.get(identifier)['state'], 'INSTALLED')

    def test_reacquire_refused_without_inventory_state(self):
        self.execute()
        unconfirmed = {k: v for k, v in self.preflight.items() if k != 'inventory_state'}
        with self.assertRaisesRegex(LifecycleError, 'acquisition_already_completed'):
            self.store.propose(unconfirmed)

    def test_reacquire_after_confirmed_absence_keeps_history(self):
        identifier = self.execute()
        before = self.store.get(identifier)
        fresh = self.store.propose(self.preflight)
        self.assertNotEqual(fresh['id'], identifier)
        self.assertEqual(fresh['state'], 'PROPOSED')
        self.assertEqual(self.store.get(identifier), before)

    def test_reacquire_record_needs_fresh_approval(self):
        self.execute()
        fresh = self.store.propose(self.preflight)['id']
        self.assertEqual(self.store.get(fresh)['state'], 'PROPOSED')
        approved = self.store.decide(fresh, 'approved', lambda _: self.preflight)
        self.assertEqual(approved['state'], 'APPROVED')
        self.assertEqual(self.store.get(fresh)['state'], 'APPROVED')

    def test_O_active_restart_never_restarts_adapter(self):
        identifier = self.propose(); self.approve(identifier); self.store.advance(identifier)
        factory = unittest.mock.Mock(side_effect=AssertionError)
        restarted = AcquisitionLifecycle(self.path, adapter_factory=factory)
        self.assertEqual(restarted.get(identifier)['state'], 'RECOVERY_REQUIRED')
        factory.assert_not_called()

    def test_P_malformed_state_fail_closed_preserves_file(self):
        self.propose(); self.path.write_text('{broken')
        restarted = AcquisitionLifecycle(self.path)
        with self.assertRaises(LifecycleError): restarted.propose(self.preflight)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_Q_invalid_transition(self):
        identifier = self.propose()
        with self.assertRaises(LifecycleError): self.store.advance(identifier)
        with self.assertRaises(LifecycleError): self.store.cancel(identifier)
        self.assertEqual(self.store.get(identifier)['state'], 'PROPOSED')

    def test_R_no_activation_network_or_executor(self):
        before = (server.ACTIVE_PROVIDER, server.ACTIVE_MODEL)
        with patch.object(server, 'select_model', side_effect=AssertionError), patch.object(server, 'execute_action', side_effect=AssertionError), patch('urllib.request.urlopen', side_effect=AssertionError), patch('subprocess.Popen', side_effect=AssertionError):
            identifier = self.execute()
        self.assertEqual(self.store.get(identifier)['activation'], 'not_performed')
        self.assertEqual(before, (server.ACTIVE_PROVIDER, server.ACTIVE_MODEL))

    def test_stale_descriptor_or_revision(self):
        identifier = self.propose(); self.preflight['descriptor']['catalog_revision'] = 'r2'
        with self.assertRaisesRegex(LifecycleError, 'stale_or_blocked'): self.approve(identifier)
        self.assertEqual(self.store.get(identifier)['state'], 'FAILED')

    def test_status_is_read_only_and_model_cannot_approve(self):
        identifier = self.propose()
        before = self.path.read_bytes()
        self.assertEqual(self.request('/acquisitions/' + identifier, method='GET')[1]['state'], 'PROPOSED')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('acquire_catalog_model', server.ACTION_REGISTRY)
        self.assertNotIn('approve', server.TOOL_REGISTRY)
        self.assertEqual(server.tool_result('approve', {'id': identifier})[0], 'unknown_tool')

    def test_status_list_is_newest_first_and_read_only(self):
        first = self.propose(); self.now += 1
        second = self.store.propose(dict(self.preflight, descriptor=dict(self.preflight['descriptor'], artifact_id='fixture:two', exact_tag='two')))['id']
        before = self.path.read_bytes()
        self.assertEqual([row['id'] for row in self.store.list_records()], [second, first])
        self.assertEqual(self.request('/acquisitions', method='GET')[1]['acquisitions'][0]['id'], second)
        self.assertEqual(self.path.read_bytes(), before)

    def test_write_failure_disables_execution(self):
        identifier = self.propose()
        with patch('acquisition_lifecycle.os.replace', side_effect=OSError):
            with self.assertRaises(LifecycleError): self.approve(identifier)
        self.assertEqual(self.store.error, 'acquisition_state_unavailable')
        self.assertFalse(self.store.jobs)

    def test_pending_restart_expires(self):
        identifier = self.propose()
        self.assertEqual(AcquisitionLifecycle(self.path).get(identifier)['state'], 'EXPIRED')

    def test_state_capacity(self):
        self.store.records = {str(i): {'state': 'FAILED', 'preflight': {'descriptor': {'provider':'other', 'artifact_id':'other'}}} for i in range(MAX_RECORDS)}
        with self.assertRaisesRegex(LifecycleError, 'state_capacity'): self.propose()

    def test_adapter_duplicate_begin(self):
        from acquisition_lifecycle import frozen_descriptor
        adapter = SimulatedAdapter(); descriptor = frozen_descriptor(self.preflight['descriptor'])
        adapter.begin(descriptor)
        with self.assertRaises(LifecycleError): adapter.begin(descriptor)

    def test_http_noop_blocked_unknown_and_status(self):
        self.preflight.update(eligible=False, no_op=True, status='NO_OP_ALREADY_INSTALLED')
        self.assertEqual(self.request('/acquisition/models/fixture/propose')[0], 200)
        self.preflight.update(no_op=False, status='BLOCKED')
        self.assertEqual(self.request('/acquisition/models/fixture/propose')[0], 409)
        self.preflight = None
        self.assertEqual(self.request('/acquisition/models/missing/propose')[0], 404)
        self.assertEqual(self.request('/acquisitions/missing', method='GET')[0], 404)
        self.assertFalse(self.store.records)

    def test_http_denial_replay_and_override(self):
        identifier = self.propose()
        self.assertEqual(self.request('/approvals/' + identifier + '/approve', b'{}')[0], 400)
        self.assertEqual(self.request('/approvals/' + identifier + '/deny')[0], 202)
        self.assertEqual(self.request('/approvals/' + identifier + '/approve')[0], 409)
        self.assertFalse(self.store.jobs)

    def test_concurrent_approval_consumed_once(self):
        from concurrent.futures import ThreadPoolExecutor
        identifier = self.propose()
        def decision():
            try: return self.approve(identifier)['state']
            except LifecycleError: return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: decision(), range(2)))
        self.assertEqual(sorted(results), ['APPROVED', 'rejected'])

    def test_unsafe_permissions_fail_closed(self):
        self.propose(); self.path.chmod(0o644)
        self.assertIsNotNone(AcquisitionLifecycle(self.path).error)

    def test_unconfirmed_cancellation_never_claims_cancelled(self):
        identifier = self.propose(); self.approve(identifier); self.store.advance(identifier)
        with patch.object(self.store.jobs[identifier], 'request_cancel', side_effect=RuntimeError):
            row = self.store.cancel(identifier)
        self.assertEqual(row['state'], 'RECOVERY_REQUIRED')

if __name__ == '__main__': unittest.main()
