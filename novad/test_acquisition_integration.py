"""v0.12C read-only acquisition catalog and preflight integration tests."""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import acquisition as acq
import acquisition_integration as integration
import catalog
import catalog_observability as obs
import server

G = acq.GIB
SEED = Path(__file__).resolve().parent.parent / 'catalog' / 'models.json'


class Wire:
    def __init__(self, request): self.request, self.response = io.BytesIO(request), bytearray()
    def makefile(self, *_args, **_kwargs): return self.request
    def sendall(self, data): self.response.extend(data)


def response(wire):
    _headers, body = bytes(wire.response).split(b'\r\n\r\n', 1)
    return int(bytes(wire.response).split()[1]), json.loads(body)


def fit_snapshot(loaded, rating='GOOD', available=True):
    return {'provider_available': available, 'models': [
        {'provider': row['source']['provider'], 'id': row['source']['upstream_id'],
         'fit': {'rating': rating}} for row in loaded.records]}


def inventory(loaded, mode='not_installed'):
    records = []
    if mode == 'exact':
        records = [{'provider': row['source']['provider'], 'id': row['source']['upstream_id'],
                    'installed': True, 'digest': row['source']['revision_digest']} for row in loaded.records]
    elif mode == 'different':
        records = [{'provider': row['source']['provider'], 'id': row['source']['upstream_id'],
                    'installed': True, 'digest': 'different'} for row in loaded.records]
    return {'provider': 'ollama', 'status': 'ready', 'source': 'fixture', 'observed_at': None,
            'errors': [], 'records': records}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(SEED.read_text())
        self.loaded = catalog.validate_catalog(self.document)
        self.view = obs.present_catalog(self.loaded, inventory(self.loaded))
        self.catalog_id = self.loaded.records[0]['catalog_id']
        self.disk = acq.DiskSnapshot(128 * G, 64 * G)

    def build(self, view=None, fit=None, disk=None, writable=True, catalog_id=None, provider_version=None):
        return integration.build_preflight(view or self.view, fit or fit_snapshot(self.loaded),
                                           catalog_id or self.catalog_id, disk or self.disk, writable,
                                           provider_version=provider_version)

    def test_A_trusted_catalog_descriptor_resolves_from_backend_allowlist(self):
        result = self.build()
        self.assertEqual(result['descriptor']['source_trust_state'], 'TRUSTED')
        self.assertEqual(result['descriptor']['artifact_id'], self.loaded.records[0]['source']['upstream_id'])
        self.assertEqual(result['trust_basis'], 'backend_allowlist')

    def test_B_missing_acquisition_metadata_is_unverified_and_blocked(self):
        view = deepcopy(self.view); view['models'][0]['acquisition'] = None
        result = self.build(view=view)
        self.assertEqual(result['descriptor']['source_trust_state'], 'UNVERIFIED')
        self.assertFalse(result['eligible'])
        self.assertIn('SOURCE_UNVERIFIED', result['blockers'])

    def test_C_D_catalog_cannot_self_authorize_or_lose_browsing_on_bad_descriptor(self):
        for source in ('arbitrary_registry', None):
            with self.subTest(source=source):
                document = deepcopy(self.document)
                document['models'][0]['acquisition']['source'] = source
                loaded = catalog.validate_catalog(document)
                self.assertEqual(len(loaded.records), len(self.document['models']))
                self.assertEqual(loaded.status, 'partial')
                self.assertIsNone(loaded.records[0]['acquisition'])
                self.assertTrue(any('.acquisition:' in error for error in loaded.errors))

    def test_E_unknown_catalog_identity_is_clean_404(self):
        wire = Wire(b'GET /acquisition/models/unknown/preflight HTTP/1.0\r\nHost: localhost\r\n\r\n')
        with patch.object(server, 'local_acquisition_preflight', return_value=None):
            server.Handler(wire, ('127.0.0.1', 1), None)
        code, payload = response(wire)
        self.assertEqual(code, 404)
        self.assertEqual(payload['error'], 'catalog_model_not_found')

    def test_F_exact_inventory_is_no_op(self):
        view = obs.present_catalog(self.loaded, inventory(self.loaded, 'exact'))
        result = self.build(view=view)
        self.assertEqual(result['status'], 'NO_OP_ALREADY_INSTALLED')

    def test_G_different_installed_revision_blocks(self):
        view = obs.present_catalog(self.loaded, inventory(self.loaded, 'different'))
        result = self.build(view=view)
        self.assertIn('REVISION_MISMATCH', result['blockers'])

    def test_H_I_good_path_and_tight_default(self):
        good = self.build()
        tight = self.build(fit=fit_snapshot(self.loaded, 'TIGHT'))
        self.assertEqual(good['status'], 'ELIGIBLE')
        self.assertIn('TIGHT_OPT_IN_REQUIRED', tight['blockers'])

    def test_J_insufficient_storage_blocks(self):
        result = self.build(disk=acq.DiskSnapshot(128 * G, 1))
        self.assertIn('INSUFFICIENT_STORAGE', result['blockers'])

    def test_K_storage_observation_uses_configured_ollama_filesystem(self):
        root = Path(self._testMethodName).parent  # an existing harmless directory
        stat = type('Stat', (), {'f_frsize': 4096, 'f_blocks': 10, 'f_bavail': 4})()
        with patch.object(Path, 'is_symlink', return_value=False), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(integration.os, 'statvfs', return_value=stat) as observed:
            disk, writable = integration.storage_observation(root)
        observed.assert_called_once_with(root)
        self.assertEqual((disk.filesystem_total, disk.filesystem_free, writable), (40960, 16384, None))

    def test_L_provider_unavailable_blocks(self):
        result = self.build(fit=fit_snapshot(self.loaded, available=False))
        self.assertIn('PROVIDER_UNAVAILABLE', result['blockers'])

    def test_M_provider_readiness_unknown_is_preserved(self):
        result = self.build(writable=None)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('PROVIDER_READINESS_UNKNOWN', result['blockers'])

    def test_N_catalog_revision_is_passed_to_evaluator(self):
        result = self.build()
        self.assertEqual(result['catalog_revision_state'], 'CURRENT')
        self.assertEqual(result['catalog_revision_used'], self.loaded.revision)

    def test_O_P_endpoint_accepts_catalog_identity_only(self):
        valid = Wire(f'GET /acquisition/models/{self.catalog_id}/preflight HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
        with patch.object(server, 'local_acquisition_preflight', return_value={'status': 'BLOCKED'}):
            server.Handler(valid, ('127.0.0.1', 1), None)
        self.assertEqual(response(valid)[0], 200)
        bad = Wire(f'GET /acquisition/models/{self.catalog_id}/preflight?url=https://bad&expected_size=1 HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
        with patch.object(server, 'local_acquisition_preflight', side_effect=AssertionError('overrides must not be read')):
            server.Handler(bad, ('127.0.0.1', 1), None)
        self.assertEqual(response(bad)[0], 400)

    def test_Q_R_snapshot_join_is_read_only_and_has_no_provider_operation(self):
        before = deepcopy((self.view, fit_snapshot(self.loaded)))
        with patch.object(server, 'select_model', side_effect=AssertionError('switch forbidden')), \
             patch.object(server, 'save_config', side_effect=AssertionError('write forbidden')):
            result = self.build()
        self.assertEqual(result['status'], 'ELIGIBLE')
        self.assertEqual((self.view, fit_snapshot(self.loaded)), before)

    def test_S_minimum_provider_version_blocks_too_old_installed_provider(self):
        gemma = self.build(catalog_id='gemma4-12b-q4-k-m', provider_version=(0, 29, 1))
        self.assertIn('PROVIDER_TOO_OLD', gemma['blockers'])
        self.assertEqual(gemma['provider_version'], {'installed': '0.29.1', 'required': '0.30.9'})
        unread = self.build(catalog_id='gemma4-12b-q4-k-m')
        self.assertNotIn('PROVIDER_TOO_OLD', unread['blockers'])
        self.assertIn('PROVIDER_VERSION_UNKNOWN', unread['warnings'])
        self.assertEqual(unread['provider_version'], {'installed': None, 'required': '0.30.9'})
        llama = self.build(catalog_id='llama3.1-8b-q4-k-m', provider_version=(0, 29, 1))
        self.assertNotIn('PROVIDER_TOO_OLD', llama['blockers'])
        self.assertNotIn('PROVIDER_VERSION_UNKNOWN', llama['warnings'])
        self.assertEqual(llama['provider_version'], {'installed': '0.29.1', 'required': None})


if __name__ == '__main__':
    unittest.main()
