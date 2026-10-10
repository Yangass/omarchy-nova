"""catalog_repins: follow an Ollama re-publish before install, within the guard. No network."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import catalog_repins
from acquisition_fit import saved_fit
from catalog import DEFAULT_PATH, load_catalog
import catalog_observability
import server
from acquisition_lifecycle import AcquisitionLifecycle
from test_acquisition_integration import Wire, response
from test_acquisition_lifecycle import fixture as lifecycle_fixture

OBSERVED = '2026-10-10T12:00:00Z'


def shipped(catalog_id='qwen3-8b-q4-k-m'):
    return load_catalog(DEFAULT_PATH).get(catalog_id)


def manifest_bytes(marker='new'):
    return json.dumps({'schemaVersion': 2, 'config': {'size': 10, 'digest': 'sha256:c'},
                       'layers': [{'mediaType': 'application/vnd.ollama.image.model', 'size': 100,
                                   'digest': 'sha256:' + marker}]}).encode()


def digest_fetch(raw):
    calls = []
    def fetch(url, headers=None, limit=1 << 20):
        calls.append((url, headers))
        if isinstance(raw, Exception): raise raw
        return raw
    fetch.calls = calls
    return fetch


def found_for(record, raw, *, size=None, architecture=None, quantization=None, vision=None):
    props = record['properties']
    return {'name': record['acquisition']['artifact_id'], 'tag': record['acquisition']['exact_tag'],
            'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'total_size': size if size is not None else props['weight_size_bytes'] - 1000,
            'architecture': architecture or props['architecture'], 'parameter_count': props['parameter_count'],
            'context_length': props['native_context'],
            'quantization': quantization or props['quantizations'][0], 'requires_ollama': '0.30.0',
            'parser': None, 'attention': {'layers': 36, 'kv_heads': 8, 'key_length': 128, 'value_length': 128},
            'capabilities': {'chat': True, 'tools': True, 'thinking': True, 'vision': vision},
            'license_line': 'Apache License', 'basename': 'Qwen3', 'size_label': '8B'}


def http_error(code):
    return urllib.error.HTTPError('https://registry.ollama.ai/x', code, 'x', {}, io.BytesIO(b''))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / 'repinned'
        self.record = shipped()

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self, raw, found=None, record=None):
        record = record or self.record
        with patch.object(catalog_repins.registry_probe, 'probe', return_value=found) as probe:
            result = catalog_repins.refresh(record, digest_fetch=digest_fetch(raw), store=self.store, observed=OBSERVED)
        return result, probe

    def test_same_digest_changes_nothing_and_skips_the_probe(self):
        record = deepcopy(self.record); raw = manifest_bytes()
        record['acquisition']['expected_revision'] = hashlib.sha256(raw).hexdigest()
        result, probe = self.refresh(raw, record=record)
        self.assertEqual(result, {'status': 'unchanged'})
        probe.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_manifest_url_and_accept_header(self):
        fetch = digest_fetch(manifest_bytes())
        catalog_repins.live_digest('qwen3:8b', fetch)
        catalog_repins.live_digest('user/model', fetch)
        self.assertEqual(fetch.calls[0], ('https://registry.ollama.ai/v2/library/qwen3/manifests/8b',
                                          {'Accept': catalog_repins.registry_probe.MANIFEST_ACCEPT}))
        self.assertEqual(fetch.calls[1][0], 'https://registry.ollama.ai/v2/user/model/manifests/latest')

    def test_republished_same_model_is_repinned_and_overlaid(self):
        raw = manifest_bytes()
        result, _ = self.refresh(raw, found_for(self.record, raw))
        new = hashlib.sha256(raw).hexdigest()
        self.assertEqual(result['status'], 'repinned')
        self.assertEqual((result['previous_revision'], result['revision']),
                         (self.record['acquisition']['expected_revision'], new))
        [record] = catalog_repins.overlay([self.record], self.store)
        self.assertEqual(record['acquisition']['expected_revision'], new)
        self.assertEqual(record['source']['revision_digest'], new)
        self.assertEqual(record['acquisition']['expected_size_bytes'], self.record['properties']['weight_size_bytes'] - 1000)
        self.assertEqual(record['acquisition']['minimum_provider_version'], '0.30.0')
        for key in ('catalog_id', 'display_name', 'family', 'task_tags'):
            self.assertEqual(record[key], self.record[key])
        self.assertEqual(record['metadata']['provenance']['references'], self.record['metadata']['provenance']['references'])
        self.assertEqual(record['repinned'], {'previous_revision': self.record['acquisition']['expected_revision'],
                                              'observed_at': OBSERVED})
        self.assertEqual(oct(self.store.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct((self.store / 'models.json').stat().st_mode & 0o777), '0o600')

    def test_second_click_on_the_same_republish_does_not_probe_again(self):
        raw = manifest_bytes()
        self.refresh(raw, found_for(self.record, raw))
        result, probe = self.refresh(raw)
        self.assertEqual(result, {'status': 'unchanged', 'repinned': True})
        probe.assert_not_called()

    def test_gguf_spelling_change_is_the_same_family(self):
        record = deepcopy(self.record); record['properties']['architecture'] = 'gptoss'
        raw = manifest_bytes()
        result, _ = self.refresh(raw, found_for(record, raw, architecture='gpt-oss'), record=record)
        self.assertEqual(result['status'], 'repinned')

    def test_guard_blocks_a_different_model(self):
        raw = manifest_bytes()
        size = self.record['acquisition']['expected_size_bytes']
        cases = ((found_for(self.record, raw, size=size * 126 // 100), ['size_changed_too_much']),
                 (found_for(self.record, raw, size=size * 74 // 100), ['size_changed_too_much']),
                 (found_for(self.record, raw, architecture='llama'), ['architecture_changed']),
                 (found_for(self.record, raw, quantization='Q8_0'), ['quantization_changed']))
        for found, reasons in cases:
            result, _ = self.refresh(raw, found)
            self.assertEqual((result['status'], result['reasons']), ('changed_too_much', reasons))
        self.assertFalse(self.store.exists())
        result, _ = self.refresh(raw, found_for(self.record, raw, size=size * 125 // 100))
        self.assertEqual(result['status'], 'repinned')

    def test_registry_answers(self):
        self.assertEqual(self.refresh(http_error(404))[0], {'status': 'gone', 'upstream_id': 'qwen3:8b'})
        self.assertEqual(self.refresh(http_error(500))[0], {'status': 'unchecked', 'reason': 'registry_unreachable'})
        self.assertEqual(self.refresh(urllib.error.URLError('offline'))[0], {'status': 'unchecked', 'reason': 'registry_unreachable'})
        self.assertEqual(self.refresh(TimeoutError())[0], {'status': 'unchecked', 'reason': 'registry_unreachable'})
        self.assertEqual(self.refresh(b'not json')[0], {'status': 'unchecked', 'reason': 'registry_invalid'})
        self.assertEqual(self.refresh(b'[1]')[0], {'status': 'unchecked', 'reason': 'registry_invalid'})
        self.assertFalse(self.store.exists())

    def test_probe_failures_after_a_changed_digest(self):
        raw = manifest_bytes()
        with patch.object(catalog_repins.registry_probe, 'probe', side_effect=http_error(404)):
            result = catalog_repins.refresh(self.record, digest_fetch=digest_fetch(raw), store=self.store)
        self.assertEqual(result['status'], 'gone')
        with patch.object(catalog_repins.registry_probe, 'probe', side_effect=ValueError('no model layer')):
            result = catalog_repins.refresh(self.record, digest_fetch=digest_fetch(raw), store=self.store)
        self.assertEqual((result['status'], result['reasons']), ('changed_too_much', ['unreadable_model']))

    def test_a_newer_shipped_list_supersedes_the_local_repin(self):
        raw = manifest_bytes()
        self.refresh(raw, found_for(self.record, raw))
        updated = deepcopy(self.record); updated['acquisition']['expected_revision'] = 'f' * 64
        self.assertIs(catalog_repins.overlay([updated], self.store)[0], updated)

    def test_vision_tag_follows_the_declared_capability(self):
        raw = manifest_bytes()
        self.refresh(raw, found_for(self.record, raw, vision=True))
        [record] = catalog_repins.overlay([self.record], self.store)
        self.assertEqual(record['task_tags'], ['general', 'reasoning', 'vision'])
        self.assertIs(record['capabilities']['vision'], True)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.temp.name}); self.env.start()
        self.record = shipped()
        raw = manifest_bytes()
        self.new = hashlib.sha256(raw).hexdigest()
        with patch.object(catalog_repins.registry_probe, 'probe', return_value=found_for(self.record, raw)):
            catalog_repins.refresh(self.record, digest_fetch=digest_fetch(raw), observed=OBSERVED)

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def view_record(self):
        with patch.object(catalog_observability, 'local_inventory', return_value={'provider': 'ollama', 'status': 'ready', 'records': [], 'errors': []}):
            view = catalog_observability.catalog_view()
        return next(r for r in view['models'] if r['catalog_id'] == self.record['catalog_id'])

    def test_catalog_view_shows_the_repin_and_the_fit_follows_it(self):
        record = self.view_record()
        self.assertEqual(record['acquisition']['expected_revision'], self.new)
        self.assertEqual(record['installed_match']['catalog_digest'], self.new)
        self.assertEqual(record['repinned']['previous_revision'], self.record['acquisition']['expected_revision'])
        fit = saved_fit(record, {'provider_available': True, 'budget': {'operating_budget_bytes': 24 * 1024**3, 'mode': 'gpu_candidate', 'assumption': 'fixture'}})
        self.assertIsNotNone(fit)

    def test_an_invalid_local_repin_falls_back_to_the_shipped_record(self):
        path = catalog_repins.default_store() / 'models.json'
        doc = json.loads(path.read_text()); doc['models'][0]['record']['task_tags'] = ['not-a-tag']
        path.write_text(json.dumps(doc))
        record = self.view_record()
        self.assertEqual(record['acquisition']['expected_revision'], self.record['acquisition']['expected_revision'])
        self.assertNotIn('repinned', record)


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = AcquisitionLifecycle(Path(self.temp.name) / 'state.json', clock=lambda: 1000)

    def propose(self, upstream):
        wire = Wire(b'POST /acquisition/models/fixture/propose HTTP/1.0\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n')
        with patch.object(server, 'ACQUISITIONS', self.store), patch.object(server, 'refresh_shipped_pin', return_value=upstream), \
                patch.object(server, 'local_acquisition_preflight', return_value=lifecycle_fixture()):
            server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_gone_or_changed_too_much_blocks_before_any_approval(self):
        for upstream, error in (({'status': 'gone', 'upstream_id': 'fixture:one'}, 'model_gone_upstream'),
                                ({'status': 'changed_too_much', 'reasons': ['architecture_changed']}, 'model_changed_too_much_upstream')):
            code, body = self.propose(upstream)
            self.assertEqual((code, body['error'], body['upstream']), (409, error, upstream))
        self.assertFalse(self.store.records)

    def test_repinned_or_unchecked_proposes_and_says_so(self):
        for index, upstream in enumerate(({'status': 'repinned', 'revision': 'b' * 64}, {'status': 'unchecked', 'reason': 'registry_unreachable'})):
            self.store = AcquisitionLifecycle(Path(self.temp.name) / f'state{index}.json', clock=lambda: 1000)
            code, body = self.propose(upstream)
            self.assertEqual((code, body['upstream']), (201, upstream))

    def test_refresh_runs_only_for_shipped_pins(self):
        with patch.object(server.catalog_repins, 'refresh') as refresh:
            self.assertEqual(server.refresh_shipped_pin('fixture'), {'status': 'unchecked', 'reason': 'not_a_shipped_pin'})
            refresh.assert_not_called()
            refresh.return_value = {'status': 'unchanged'}
            self.assertEqual(server.refresh_shipped_pin('qwen3-8b-q4-k-m'), {'status': 'unchanged'})
            self.assertEqual(refresh.call_args[0][0]['catalog_id'], 'qwen3-8b-q4-k-m')


if __name__ == '__main__':
    unittest.main()
