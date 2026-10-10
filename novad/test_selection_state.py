"""Isolated no-selection and chat-boundary regression tests."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

import server


class Wire:
    def __init__(self, request: bytes):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


def response(wire):
    headers, body = bytes(wire.response).split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body)


class FixtureProvider:
    def __init__(self):
        self.calls = []

    def models(self):
        self.calls.append('models')
        return [{'id': 'fixture:latest', 'available': True}]

    def is_loaded(self, model, timeout=3):
        self.calls.append(('is_loaded', model))
        return False

    def stream_chat(self, *_args):
        raise AssertionError('chat provider must not be used without a selection')


class SelectionStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_config = server.CONFIG_PATH
        self.old_providers = server.PROVIDERS
        self.old_selection = server.ACTIVE_PROVIDER, server.ACTIVE_MODEL
        self.config = Path(self.temporary.name) / 'model.json'
        self.provider = FixtureProvider()
        server.CONFIG_PATH = self.config
        server.PROVIDERS = {'ollama': self.provider}
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = None, None
        server.RUNTIME.set('idle')

    def tearDown(self):
        server.CONFIG_PATH = self.old_config
        server.PROVIDERS = self.old_providers
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = self.old_selection
        server.RUNTIME.set('idle')
        self.temporary.cleanup()

    def write(self, value):
        self.config.write_text(json.dumps(value))

    def request(self, path):
        body = json.dumps({'message': 'hello'}).encode()
        raw = (f'POST {path} HTTP/1.0\r\nHost: localhost\r\n'
               f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body
        wire = Wire(raw)
        server.Handler(wire, ('127.0.0.1', 1), None)
        return response(wire)

    def test_missing_malformed_and_unsupported_config_restart_unselected(self):
        for value in (None, '{', '[]', json.dumps({'active_provider': ['ollama'],
                                                   'active_model': 'fixture:latest'}),
                      json.dumps({'active_provider': 'ollama', 'active_model': ''}),
                      json.dumps({'active_provider': 'cloud',
                                  'active_model': 'fixture:latest'})):
            if value is None:
                self.config.unlink(missing_ok=True)
            else:
                self.config.write_text(value)
            server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = 'ollama', 'stale-fallback'
            server.load_config()
            self.assertEqual((server.ACTIVE_PROVIDER, server.ACTIVE_MODEL), (None, None))

    def test_valid_explicit_config_survives_restart(self):
        self.write({'active_provider': 'ollama', 'active_model': 'fixture:latest'})
        server.load_config()
        self.assertEqual((server.ACTIVE_PROVIDER, server.ACTIVE_MODEL),
                         ('ollama', 'fixture:latest'))

    def test_no_selection_is_explicit_in_active_models_and_runtime_state(self):
        active = server.active_model_state()
        self.assertEqual(active['selection_state'], 'none')
        self.assertEqual(active['error'], 'model_selection_required')
        self.assertIsNone(active['provider'])
        self.assertIsNone(active['model'])
        self.assertFalse(active['runtime_ready'])

        wire = Wire(b'GET /models HTTP/1.0\r\nHost: localhost\r\n\r\n')
        server.Handler(wire, ('127.0.0.1', 1), None)
        status, payload = response(wire)
        self.assertEqual(status, 200)
        self.assertIsNone(payload['active_provider'])
        self.assertIsNone(payload['active_model'])
        self.assertEqual(payload['selection_state'], 'none')
        self.assertEqual(payload['models'][0]['id'], 'fixture:latest')

        wire = Wire(b'GET /state HTTP/1.0\r\nHost: localhost\r\n\r\n')
        server.Handler(wire, ('127.0.0.1', 1), None)
        status, payload = response(wire)
        self.assertEqual(status, 200)
        self.assertEqual(payload['selection'],
                         {'configured': False, 'provider': None, 'model': None})

    def test_active_model_endpoint_treats_no_selection_as_setup_state(self):
        wire = Wire(b'GET /model/active HTTP/1.0\r\nHost: localhost\r\n\r\n')
        server.Handler(wire, ('127.0.0.1', 1), None)
        status, payload = response(wire)
        self.assertEqual(status, 200)
        self.assertEqual(payload['error'], 'model_selection_required')
        self.assertEqual(payload['status'], 'setup_required')
        self.assertEqual(self.provider.calls, [])

    def test_both_chat_endpoints_return_structured_setup_required_without_provider_use(self):
        for path in ('/chat', '/chat/stream'):
            status, payload = self.request(path)
            self.assertEqual(status, 409)
            self.assertEqual(payload['error'], 'model_selection_required')
            self.assertEqual(payload['status'], 'setup_required')
        self.assertEqual(self.provider.calls, [])

    def test_direct_agent_loop_has_no_hidden_fallback(self):
        with self.assertRaisesRegex(server.OllamaFailure, 'must be selected'):
            server.run_agent_loop('hello')
        self.assertEqual(self.provider.calls, [])


if __name__ == '__main__':
    unittest.main()
