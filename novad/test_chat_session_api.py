"""Isolated CHAT-02B lifecycle API tests; chat and providers are not called."""

import io
import json
import unittest

import chat_sessions
import server


class Wire:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


def request(method, path, body=b'', headers=()):
    values = [f'{method} {path} HTTP/1.0', 'Host: localhost']
    values.extend(headers)
    if body and not any(value.lower().startswith('content-length:') for value in values):
        values.append(f'Content-Length: {len(body)}')
    wire = Wire(('\r\n'.join(values) + '\r\n\r\n').encode() + body)
    server.Handler(wire, ('127.0.0.1', 1), None)
    raw_headers, raw_body = bytes(wire.response).split(b'\r\n\r\n', 1)
    header_lines = raw_headers.decode().split('\r\n')
    response_headers = {}
    for line in header_lines[1:]:
        name, value = line.split(':', 1)
        response_headers[name.lower()] = value.strip()
    return int(header_lines[0].split()[1]), response_headers, json.loads(raw_body)


class ChatSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.original = server.CHAT_SESSIONS
        server.CHAT_SESSIONS = chat_sessions.ChatSessionStore()

    def tearDown(self):
        server.CHAT_SESSIONS = self.original

    def create(self):
        status, headers, payload = request('POST', '/chat/sessions')
        self.assertEqual(status, 201)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        return payload

    def test_create_status_and_idle_reset(self):
        created = self.create()
        identifier = created['session_id']
        self.assertFalse(created['busy'])
        self.assertEqual(created['history']['retained_turns'], 0)
        self.assertEqual(created['history']['max_turns'], chat_sessions.MAX_TURNS)
        self.assertEqual(created['history']['max_serialized_bytes'],
                         chat_sessions.MAX_HISTORY_BYTES)
        self.assertEqual(created['history']['storage_encoding'], 'compact_utf8_json')
        self.assertIsNone(created['last_completion'])

        begun = server.CHAT_SESSIONS.begin(identifier, 'private user text')
        server.CHAT_SESSIONS.complete(identifier, begun.turn_id,
                                      'private assistant text')
        status, _headers, current = request('GET', f'/chat/sessions/{identifier}')
        self.assertEqual(status, 200)
        self.assertEqual(current['history']['retained_turns'], 1)
        self.assertGreater(current['history']['serialized_bytes'], 2)
        self.assertEqual(current['last_completion']['turn_id'], begun.turn_id)
        self.assertEqual(current['last_completion']['retention'], 'RETAINED')
        encoded = json.dumps(current)
        self.assertNotIn('private user text', encoded)
        self.assertNotIn('private assistant text', encoded)

        status, _headers, reset = request(
            'POST', f'/chat/sessions/{identifier}/reset')
        self.assertEqual(status, 200)
        self.assertFalse(reset['busy'])
        self.assertEqual(reset['history']['retained_turns'], 0)
        self.assertEqual(reset['history']['serialized_bytes'], 2)
        self.assertIsNone(reset['last_completion'])

    def test_independent_sessions_report_independent_metadata(self):
        first, second = self.create()['session_id'], self.create()['session_id']
        begun = server.CHAT_SESSIONS.begin(first, 'one')
        server.CHAT_SESSIONS.complete(first, begun.turn_id, 'answer')
        first_status = request('GET', f'/chat/sessions/{first}')[2]
        second_status = request('GET', f'/chat/sessions/{second}')[2]
        self.assertEqual(first_status['history']['retained_turns'], 1)
        self.assertEqual(second_status['history']['retained_turns'], 0)

    def test_unknown_session_is_not_created_implicitly(self):
        identifier = 'chat_' + 'A' * 32
        for method, path in (
                ('GET', f'/chat/sessions/{identifier}'),
                ('POST', f'/chat/sessions/{identifier}/reset')):
            with self.subTest(method=method):
                status, _headers, payload = request(method, path)
                self.assertEqual(status, 404)
                self.assertEqual(payload, {'error': 'unknown_session'})
        self.assertEqual(self.create()['history']['retained_turns'], 0)

    def test_busy_session_status_and_reset_conflict(self):
        identifier = self.create()['session_id']
        begun = server.CHAT_SESSIONS.begin(identifier, 'active turn')
        status, _headers, current = request('GET', f'/chat/sessions/{identifier}')
        self.assertEqual(status, 200)
        self.assertTrue(current['busy'])
        status, _headers, payload = request(
            'POST', f'/chat/sessions/{identifier}/reset')
        self.assertEqual(status, 409)
        self.assertEqual(payload, {'error': 'busy_session'})
        self.assertTrue(server.CHAT_SESSIONS.snapshot(identifier).busy)
        server.CHAT_SESSIONS.abort(identifier, begun.turn_id)

    def test_capacity_exhaustion_is_explicit_when_all_sessions_are_busy(self):
        server.CHAT_SESSIONS = chat_sessions.ChatSessionStore(max_sessions=2)
        identifiers = [self.create()['session_id'], self.create()['session_id']]
        turns = [server.CHAT_SESSIONS.begin(identifier, 'active')
                 for identifier in identifiers]
        status, _headers, payload = request('POST', '/chat/sessions')
        self.assertEqual(status, 503)
        self.assertEqual(payload, {'error': 'session_capacity_exhausted'})
        for identifier, turn in zip(identifiers, turns):
            server.CHAT_SESSIONS.abort(identifier, turn.turn_id)

    def test_malformed_lifecycle_requests_are_rejected(self):
        identifier = self.create()['session_id']
        cases = (
            ('POST', '/chat/sessions?shared=true', b''),
            ('POST', '/chat/sessions', b'{}'),
            ('GET', '/chat/sessions', b''),
            ('GET', '/chat/sessions/not-an-id', b''),
            ('GET', f'/chat/sessions/{identifier}?details=true', b''),
            ('GET', f'/chat/sessions/{identifier}', b'{}'),
            ('POST', f'/chat/sessions/{identifier}', b''),
            ('POST', f'/chat/sessions/{identifier}/reset', b'{}'),
            ('POST', f'/chat/sessions/{identifier}/reset/extra', b''),
        )
        for method, path, body in cases:
            with self.subTest(method=method, path=path):
                status, _headers, payload = request(method, path, body)
                if method == 'GET' and path == '/chat/sessions':
                    self.assertEqual((status, payload),
                                     (404, {'error': 'Unknown endpoint.'}))
                else:
                    self.assertEqual((status, payload),
                                     (400, {'error': 'invalid_chat_session_request'}))

        status, _headers, payload = request(
            'POST', '/chat/sessions', headers=('Transfer-Encoding: chunked',))
        self.assertEqual((status, payload),
                         (400, {'error': 'invalid_chat_session_request'}))


if __name__ == '__main__':
    unittest.main()
