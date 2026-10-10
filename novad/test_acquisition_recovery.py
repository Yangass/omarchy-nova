"""P1-01E.4 recovery tests. All provider observations are isolated fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from acquisition_lifecycle import (AcquisitionLifecycle, LifecycleError,
    OPERATOR_INACTIVE_ATTESTATION)
from test_acquisition_integration import Wire, response
from test_acquisition_lifecycle import fixture
import server


TRANSFER = {
    'bytes_transferred': None,
    'provider_completed_bytes': 24,
    'provider_total_bytes': 100,
    'current_blob_index': 1,
    'current_blob_completed_bytes': 24,
    'current_blob_total_bytes': 100,
}


class FailingRealAdapter:
    simulation = False
    cancellation_supported = False

    def __init__(self): self.started = False; self.step = 0
    def begin(self, descriptor): self.started = True
    def observe_progress(self):
        self.step += 1
        if self.step == 1:
            return {'state': 'downloading', 'progress': None, 'transfer': dict(TRANSFER),
                    'transfer_observed_at': 1001}
        return {'state': 'failed', 'progress': None, 'transfer': dict(TRANSFER),
                'transfer_observed_at': 1001,
                'failure_detail': {'stage': 'provider_transport', 'code': 'transport_error',
                                   'http_status': None, 'provider_message': None,
                                   'provider_activity': 'UNKNOWN'}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'state.json'
        self.now = 1000
        self.preflight = fixture()
        self.store = AcquisitionLifecycle(self.path, clock=lambda: self.now,
                                          adapter_factory=FailingRealAdapter)
        self.identifier = self.store.propose(self.preflight)['id']
        self.store.decide(self.identifier, 'approved', lambda _: self.preflight)
        self.store.advance(self.identifier)
        self.now = 1001; self.store.advance(self.identifier)
        self.now = 1002; self.store.advance(self.identifier)
        self.assertEqual(self.store.get(self.identifier)['state'], 'RECOVERY_REQUIRED')

    def assess(self, state, reason=None):
        value = {'state': state}
        if reason: value['reason'] = reason
        return self.store.assess_recovery(self.identifier, lambda _: value)

    def resolve(self, assessment, state='absent', attestation=OPERATOR_INACTIVE_ATTESTATION):
        return self.store.resolve_recovery(self.identifier, assessment['assessment_id'],
                                           attestation, lambda _: {'state': state})

    def request(self, path, body=b'', method='GET', observation=None):
        wire = Wire(f'{method} {path} HTTP/1.0\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
        with patch.object(server, 'ACQUISITIONS', self.store), patch.object(
                server, 'inspect_artifact', return_value=observation or {'state': 'absent'}):
            server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_failed_attempt_and_last_progress_are_preserved_separately(self):
        row = self.store.get(self.identifier)
        self.assertEqual((row['state'], row['error'], row['verification']),
                         ('RECOVERY_REQUIRED', 'provider_failure', 'pending'))
        self.assertIsNone(row['progress'])
        self.assertEqual(row['historical_progress'],
                         {'progress': TRANSFER, 'observed_at': 1001})
        self.assertEqual(row['failure_detail']['code'], 'transport_error')
        self.assertEqual(row['failure_detail']['provider_activity'], 'UNKNOWN')
        persisted = json.loads(self.path.read_text())
        self.assertEqual(persisted['records'][self.identifier]['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(persisted['progress_history'][self.identifier]['progress'], TRANSFER)

    def test_absent_unknown_activity_requires_operator_attestation(self):
        assessment = self.assess('absent')
        self.assertEqual(assessment['artifact_state'], 'ABSENT')
        self.assertTrue(assessment['operator_attestation_required'])
        with self.assertRaisesRegex(LifecycleError, 'operator_attestation_required'):
            self.resolve(assessment, attestation=None)
        resolution = self.resolve(assessment)
        self.assertEqual((resolution['outcome'], resolution['basis'], resolution['retry_allowed']),
                         ('RETRY_ALLOWED', 'OPERATOR_ATTESTATION', True))
        row = self.store.get(self.identifier)
        self.assertEqual((row['state'], row['error']), ('RECOVERY_REQUIRED', 'provider_failure'))
        self.assertEqual(row['recovery_resolution']['id'], resolution['id'])

    def test_new_attempt_after_resolution_has_fresh_identity_and_approval(self):
        assessment = self.assess('absent'); self.resolve(assessment)
        second = self.store.propose(self.preflight)
        self.assertNotEqual(second['id'], self.identifier)
        self.assertEqual(second['state'], 'PROPOSED')
        approved = self.store.decide(second['id'], 'approved', lambda _: self.preflight)
        self.assertEqual(approved['state'], 'APPROVED')
        self.assertEqual(self.store.get(self.identifier)['state'], 'RECOVERY_REQUIRED')
        self.assertFalse(self.store.jobs)

    def test_exact_identity_records_resolution_without_rewriting_failure(self):
        assessment = self.assess('exact')
        resolution = self.resolve(assessment, state='exact', attestation=None)
        self.assertEqual((resolution['outcome'], resolution['retry_allowed']),
                         ('EXACT_INSTALLED', False))
        self.assertEqual(self.store.get(self.identifier)['state'], 'RECOVERY_REQUIRED')
        with self.assertRaisesRegex(LifecycleError, 'acquisition_conflict'):
            self.store.propose(self.preflight)

    def test_mismatch_and_unverifiable_inventory_remain_distinct_and_blocked(self):
        mismatch = self.assess('mismatch')
        self.assertEqual(mismatch['artifact_state'], 'MISMATCH')
        with self.assertRaisesRegex(LifecycleError, 'artifact_revision_mismatch'):
            self.resolve(mismatch, state='mismatch', attestation=None)
        unavailable = self.assess('unknown', 'provider_unavailable')
        self.assertEqual((unavailable['artifact_state'], unavailable['artifact_detail']),
                         ('UNVERIFIABLE', 'provider_unavailable'))
        with self.assertRaisesRegex(LifecycleError, 'artifact_identity_unverifiable'):
            self.store.resolve_recovery(self.identifier, unavailable['assessment_id'], None,
                                        lambda _: {'state': 'unknown', 'reason': 'provider_unavailable'})
        self.assertIsNone(self.store.get(self.identifier)['recovery_resolution'])

    def test_duplicate_and_stale_resolution_requests_are_rejected(self):
        stale = self.assess('absent')
        with self.assertRaisesRegex(LifecycleError, 'stale_recovery_assessment'):
            self.store.resolve_recovery(self.identifier, stale['assessment_id'], None,
                                        lambda _: {'state': 'exact'})
        current = self.assess('absent'); self.resolve(current)
        with self.assertRaisesRegex(LifecycleError, 'recovery_already_resolved'):
            self.resolve(current)

    def test_known_active_acquisition_blocks_resolution(self):
        second_preflight = fixture()
        second_preflight['descriptor']['artifact_id'] = 'fixture:two'
        second_preflight['descriptor']['exact_tag'] = 'two'
        other = self.store.propose(second_preflight)['id']
        self.store.decide(other, 'approved', lambda _: second_preflight)
        assessment = self.assess('absent')
        with self.assertRaisesRegex(LifecycleError, 'acquisition_active'):
            self.resolve(assessment)

    def test_provider_activity_evidence_controls_absent_resolution(self):
        self.store.failure_details[self.identifier]['provider_activity'] = 'ACTIVE'
        self.store._save()
        active = self.assess('absent')
        self.assertEqual(active['provider_activity'], 'ACTIVE')
        with self.assertRaisesRegex(LifecycleError, 'provider_acquisition_active'):
            self.resolve(active, attestation=None)
        self.store.failure_details[self.identifier]['provider_activity'] = 'INACTIVE_CONFIRMED'
        self.store._save()
        inactive = self.assess('absent')
        self.assertFalse(inactive['operator_attestation_required'])
        resolution = self.resolve(inactive, attestation=None)
        self.assertEqual((resolution['basis'], resolution['operator_attestation']),
                         ('MACHINE_CONFIRMED_INACTIVE', False))

    def test_resolution_survives_restart_without_provider_work(self):
        assessment = self.assess('absent'); resolution = self.resolve(assessment)
        with patch.object(FailingRealAdapter, 'begin', side_effect=AssertionError):
            restarted = AcquisitionLifecycle(self.path, clock=lambda: 2000,
                                              adapter_factory=FailingRealAdapter)
        self.assertEqual(restarted.get(self.identifier)['recovery_resolution']['id'], resolution['id'])
        self.assertEqual(restarted.get(self.identifier)['historical_progress']['progress'], TRANSFER)

    def test_version_one_state_remains_readable_until_a_real_mutation(self):
        document = json.loads(self.path.read_text())
        self.path.write_text(json.dumps({'version': 1, 'records': document['records']}))
        legacy = AcquisitionLifecycle(self.path, clock=lambda: 2000,
                                      adapter_factory=FailingRealAdapter)
        self.assertEqual(legacy.get(self.identifier)['state'], 'RECOVERY_REQUIRED')
        self.assertIsNone(legacy.get(self.identifier)['historical_progress'])
        self.assertEqual(json.loads(self.path.read_text())['version'], 1)
        assessment = legacy.assess_recovery(self.identifier, lambda _: {'state': 'absent'})
        legacy.resolve_recovery(self.identifier, assessment['assessment_id'],
                                OPERATOR_INACTIVE_ATTESTATION, lambda _: {'state': 'absent'})
        self.assertEqual(json.loads(self.path.read_text())['version'], 2)

    def test_read_only_assessment_and_strict_http_resolution(self):
        before = self.path.read_bytes()
        code, assessment = self.request('/acquisitions/' + self.identifier + '/recovery')
        self.assertEqual((code, assessment['artifact_state']), (200, 'ABSENT'))
        self.assertEqual(self.path.read_bytes(), before)
        body = json.dumps({'assessment_id': assessment['assessment_id'],
                           'operator_attestation': OPERATOR_INACTIVE_ATTESTATION}).encode()
        code, resolution = self.request('/acquisitions/' + self.identifier + '/resolve-recovery', body, 'POST')
        self.assertEqual((code, resolution['outcome']), (201, 'RETRY_ALLOWED'))
        code, _ = self.request('/acquisitions/' + self.identifier + '/resolve-recovery', body, 'POST')
        self.assertEqual(code, 409)
        bad = json.dumps({'assessment_id': assessment['assessment_id'], 'url': 'https://example.test'}).encode()
        self.assertEqual(self.request('/acquisitions/' + self.identifier + '/resolve-recovery', bad, 'POST')[0], 400)


if __name__ == '__main__': unittest.main()
