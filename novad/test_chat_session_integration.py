"""Isolated CHAT-02C chat/session integration; no provider service is called."""

from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

import chat_sessions
import server


OMIT = object()


class Wire:
    def __init__(self, request, fail_marker=None):
        self.request = io.BytesIO(request)
        self.response = bytearray()
        self.fail_marker = fail_marker

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, data):
        if self.fail_marker is not None and self.fail_marker in data:
            raise BrokenPipeError('fixture disconnect')
        self.response.extend(data)


def handle(method, path, body=b'', fail_marker=None):
    raw = (f'{method} {path} HTTP/1.0\r\nHost: localhost\r\n'
           f'Content-Length: {len(body)}\r\n\r\n').encode() + body
    wire = Wire(raw, fail_marker)
    server.Handler(wire, ('127.0.0.1', 1), None)
    return wire


def post_chat(path, message, session_id=OMIT, fail_marker=None):
    payload = {'message': message}
    if session_id is not OMIT:
        payload['session_id'] = session_id
    return handle('POST', path, json.dumps(payload).encode(), fail_marker)


def status_and_body(wire):
    headers, body = bytes(wire.response).split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), body


def json_response(wire):
    status, body = status_and_body(wire)
    return status, json.loads(body)


def sse_events(wire):
    status, body = status_and_body(wire)
    events = []
    for frame in body.decode().split('\n\n'):
        if not frame.strip():
            continue
        event, data = 'message', ''
        for line in frame.splitlines():
            if line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data += line[5:].strip()
        events.append((event, json.loads(data)))
    return status, events


def terminal(wire, path):
    if path == '/chat':
        return json_response(wire)
    status, events = sse_events(wire)
    return status, next(payload for event, payload in events if event == 'done')


def model_reply(content):
    return {'role': 'assistant', 'content': content, 'tool_calls': []}


def tool_call(name):
    return {'role': 'assistant', 'content': '',
            'tool_calls': [{'function': {'name': name, 'arguments': {}}}]}


class ChatSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.old_store = server.CHAT_SESSIONS
        self.old_selection = server.ACTIVE_PROVIDER, server.ACTIVE_MODEL
        server.CHAT_SESSIONS = chat_sessions.ChatSessionStore()
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = 'ollama', 'fixture:model'
        server.RUNTIME.set('idle')
        self.assertFalse(server.MODEL_OPERATION_LOCK.locked())

    def tearDown(self):
        self.assertFalse(server.MODEL_OPERATION_LOCK.locked())
        server.CHAT_SESSIONS = self.old_store
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = self.old_selection
        server.RUNTIME.set('idle')

    def session(self):
        return server.CHAT_SESSIONS.create()

    def test_second_turn_receives_completed_history_for_json_and_sse(self):
        for path in ('/chat', '/chat/stream'):
            with self.subTest(path=path):
                server.CHAT_SESSIONS = chat_sessions.ChatSessionStore()
                identifier = self.session()
                captured = []
                replies = iter((model_reply('first answer'), model_reply('second answer')))

                def model_turn(messages, *_args):
                    captured.append(deepcopy(messages))
                    return next(replies)

                with patch.object(server, 'stream_model_turn', side_effect=model_turn):
                    first = post_chat(path, 'first question', identifier)
                    first_status, first_terminal = terminal(first, path)
                    second = post_chat(path, 'follow up', identifier)
                    second_status, second_terminal = terminal(second, path)
                self.assertEqual((first_status, second_status), (200, 200))
                self.assertEqual(first_terminal['session_id'], identifier)
                self.assertEqual(second_terminal['session_id'], identifier)
                self.assertEqual(captured[1][1:], [
                    {'role': 'user', 'content': 'first question'},
                    {'role': 'assistant', 'content': 'first answer'},
                    {'role': 'user', 'content': 'follow up'},
                ])
                self.assertIn('historical context, not current telemetry',
                              captured[1][0]['content'])
                snapshot = server.CHAT_SESSIONS.snapshot(identifier)
                self.assertEqual(snapshot.last_completion.turn_id,
                                 second_terminal['turn_id'])
                self.assertEqual(snapshot.last_completion.retention, 'RETAINED')

    def test_independent_sessions_do_not_share_provider_context(self):
        first, second = self.session(), self.session()
        for identifier, user, answer in ((first, 'private one', 'answer one'),
                                         (second, 'private two', 'answer two')):
            begun = server.CHAT_SESSIONS.begin(identifier, user)
            server.CHAT_SESSIONS.complete(identifier, begun.turn_id, answer)
        captured = []

        def model_turn(messages, *_args):
            captured.append(deepcopy(messages))
            return model_reply('continued')

        with patch.object(server, 'stream_model_turn', side_effect=model_turn):
            post_chat('/chat', 'continue first', first)
            post_chat('/chat', 'continue second', second)
        self.assertIn({'role': 'user', 'content': 'private one'}, captured[0])
        self.assertNotIn({'role': 'user', 'content': 'private two'}, captured[0])
        self.assertIn({'role': 'user', 'content': 'private two'}, captured[1])
        self.assertNotIn({'role': 'user', 'content': 'private one'}, captured[1])

    def test_omitted_session_id_remains_stateless_for_both_endpoints(self):
        for path in ('/chat', '/chat/stream'):
            with self.subTest(path=path):
                captured = []

                def model_turn(messages, *_args):
                    captured.append(deepcopy(messages))
                    return model_reply('answer')

                with patch.object(server, 'stream_model_turn', side_effect=model_turn):
                    first = post_chat(path, 'first')
                    second = post_chat(path, 'second')
                self.assertEqual(captured[1][1:],
                                 [{'role': 'user', 'content': 'second'}])
                for wire in (first, second):
                    _status, value = terminal(wire, path)
                    self.assertNotIn('session_id', value)
                    self.assertNotIn('turn_id', value)
                    if path == '/chat':
                        self.assertEqual(set(value),
                                         {'provider', 'model', 'message', 'done'})

    def test_invalid_unknown_and_busy_sessions_fail_before_agent_work(self):
        identifier = self.session()
        active = server.CHAT_SESSIONS.begin(identifier, 'already active')
        unknown = 'chat_' + 'A' * 32
        with patch.object(server, 'run_agent_loop') as agent:
            invalid = post_chat('/chat', 'hello', 'invalid')
            missing = post_chat('/chat/stream', 'hello', unknown)
            busy = post_chat('/chat', 'hello', identifier)
        self.assertEqual(json_response(invalid),
                         (400, {'error': 'invalid_chat_session_id'}))
        self.assertEqual(json_response(missing),
                         (404, {'error': 'unknown_session'}))
        self.assertEqual(json_response(busy),
                         (409, {'error': 'busy_session'}))
        agent.assert_not_called()
        server.CHAT_SESSIONS.abort(identifier, active.turn_id)

    def test_global_model_busy_rejection_aborts_new_session_lease(self):
        identifier = self.session()
        self.assertTrue(server.MODEL_OPERATION_LOCK.acquire(blocking=False))
        try:
            response = post_chat('/chat', 'cannot start now', identifier)
            self.assertEqual(json_response(response)[0], 409)
            self.assertEqual(json_response(response)[1]['error'], 'model_busy')
            self.assertFalse(server.CHAT_SESSIONS.snapshot(identifier).busy)
        finally:
            server.MODEL_OPERATION_LOCK.release()

    def test_reset_during_request_observes_busy_session(self):
        identifier = self.session()
        reset_result = []

        def agent(*_args, **_kwargs):
            reset_result.append(json_response(handle(
                'POST', f'/chat/sessions/{identifier}/reset')))
            return 'finished'

        with patch.object(server, 'run_agent_loop', side_effect=agent):
            response = post_chat('/chat', 'hold session', identifier)
        self.assertEqual(reset_result,
                         [(409, {'error': 'busy_session'})])
        self.assertEqual(json_response(response)[0], 200)
        self.assertFalse(server.CHAT_SESSIONS.snapshot(identifier).busy)

    def test_session_remains_busy_while_action_approval_waits(self):
        identifier = self.session()
        responses = iter((tool_call('restart_nova_actions'),
                          model_reply('Action declined.')))
        reset_result = []

        def wait(_pending):
            reset_result.append(json_response(handle(
                'POST', f'/chat/sessions/{identifier}/reset')))
            return 'denied'

        with patch.object(server, 'stream_model_turn', side_effect=responses), \
                patch.object(server.APPROVALS, 'wait', side_effect=wait), \
                patch.object(server, 'execute_action') as execute:
            response = post_chat('/chat', 'restart diagnostics', identifier)
        self.assertEqual(reset_result,
                         [(409, {'error': 'busy_session'})])
        self.assertEqual(json_response(response)[0], 200)
        execute.assert_not_called()
        self.assertEqual(server.CHAT_SESSIONS.snapshot(identifier).history,
                         (chat_sessions.Turn('restart diagnostics',
                                             'Action declined.'),))

    def test_failures_disconnects_empty_output_and_restart_abort_sessions(self):
        cases = (
            ('provider', server.OllamaFailure('fixture failure'), None),
            ('empty', '   ', None),
            ('restart', server.SelfRestart(), None),
            ('terminal_disconnect', 'finished', b'"done":true'),
        )
        for path in ('/chat', '/chat/stream'):
            for name, result, marker in cases:
                with self.subTest(path=path, case=name):
                    server.CHAT_SESSIONS = chat_sessions.ChatSessionStore()
                    identifier = self.session()
                    behavior = (patch.object(server, 'run_agent_loop', side_effect=result)
                                if isinstance(result, Exception)
                                else patch.object(server, 'run_agent_loop', return_value=result))
                    with behavior:
                        post_chat(path, 'will not retain', identifier,
                                  fail_marker=marker)
                    snapshot = server.CHAT_SESSIONS.snapshot(identifier)
                    self.assertFalse(snapshot.busy)
                    self.assertEqual(snapshot.history, ())
                    self.assertIsNone(snapshot.last_completion)
                    self.assertFalse(server.MODEL_OPERATION_LOCK.locked())
                    server.RUNTIME.set('idle')

    def test_stream_disconnect_during_partial_output_aborts_turn(self):
        identifier = self.session()

        def agent(_message, on_token, *_args, **_kwargs):
            on_token('partial output')
            return 'finished'

        with patch.object(server, 'run_agent_loop', side_effect=agent):
            post_chat('/chat/stream', 'disconnect', identifier,
                      fail_marker=b'event: token')
        snapshot = server.CHAT_SESSIONS.snapshot(identifier)
        self.assertFalse(snapshot.busy)
        self.assertEqual(snapshot.history, ())

    def test_later_history_excludes_raw_tool_messages_and_results(self):
        identifier = self.session()
        responses = iter((
            tool_call('get_gpu_usage'),
            model_reply('Earlier telemetry was explained.'),
            model_reply('Follow-up answer.'),
        ))
        captured = []

        def model_turn(messages, *_args):
            captured.append(deepcopy(messages))
            return next(responses)

        with patch.object(server, 'stream_model_turn', side_effect=model_turn), \
                patch.object(server, 'tool_result', return_value=(
                    'get_gpu_usage', {'ok': True, 'data': {'private': 73}})):
            post_chat('/chat', 'inspect telemetry', identifier)
            post_chat('/chat', 'explain again', identifier)
        later = captured[2]
        self.assertEqual([item['role'] for item in later],
                         ['system', 'user', 'assistant', 'user'])
        self.assertNotIn('private', json.dumps(later))
        self.assertNotIn('tool', [item['role'] for item in later])

    def test_historical_action_result_never_reuses_approval(self):
        for previous in ('approved', 'denied'):
            with self.subTest(previous=previous):
                server.CHAT_SESSIONS = chat_sessions.ChatSessionStore()
                identifier = self.session()
                responses = iter((
                    tool_call('restart_nova_actions'),
                    model_reply('Earlier action finished.'),
                    tool_call('restart_nova_actions'),
                    model_reply('Current action was denied.'),
                ))
                captured = []

                def model_turn(messages, *_args):
                    captured.append(deepcopy(messages))
                    return next(responses)

                with patch.object(server, 'stream_model_turn', side_effect=model_turn), \
                        patch.object(server.APPROVALS, 'create',
                                     wraps=server.APPROVALS.create) as create, \
                        patch.object(server.APPROVALS, 'wait',
                                     side_effect=(previous, 'denied')), \
                        patch.object(server, 'execute_action',
                                     return_value={'status': 'success'}) as execute:
                    post_chat('/chat', 'restart it once', identifier)
                    post_chat('/chat', 'restart it again', identifier)
                self.assertEqual(create.call_count, 2)
                self.assertEqual(execute.call_count, 1 if previous == 'approved' else 0)
                self.assertEqual([item['role'] for item in captured[2]],
                                 ['system', 'user', 'assistant', 'user'])
                historical = captured[2][1:]
                self.assertFalse(any('tool_name' in item or item['role'] == 'tool'
                                     for item in historical))
                self.assertNotIn('"status":"approved"',
                                 json.dumps(historical, separators=(',', ':')))
                self.assertNotIn('"status":"denied"',
                                 json.dumps(historical, separators=(',', ':')))

    def test_status_reports_retention_only_after_terminal_completion(self):
        identifier = self.session()
        answer = 'x' * (chat_sessions.MAX_HISTORY_BYTES + 100)
        observed_while_running = []

        def agent(*_args, **_kwargs):
            observed_while_running.append(
                server.CHAT_SESSIONS.snapshot(identifier).last_completion)
            return answer

        with patch.object(server, 'run_agent_loop', side_effect=agent):
            response = post_chat('/chat', 'large answer', identifier)
        status, payload = json_response(response)
        self.assertEqual(status, 200)
        self.assertEqual(observed_while_running, [None])
        status_code, status_payload = json_response(handle(
            'GET', f'/chat/sessions/{identifier}'))
        self.assertEqual(status_code, 200)
        self.assertEqual(status_payload['last_completion']['turn_id'],
                         payload['turn_id'])
        self.assertEqual(status_payload['last_completion']['retention'],
                         'OMITTED_OVERSIZED')
        self.assertEqual(status_payload['history']['retained_turns'], 0)


if __name__ == '__main__':
    unittest.main()
