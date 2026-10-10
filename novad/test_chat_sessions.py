"""Isolated CHAT-02A session-store tests; no services or models are used."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import unittest

import chat_sessions as sessions


def complete_turn(store, session_id, user, assistant):
    begun = store.begin(session_id, user)
    return store.complete(session_id, begun.turn_id, assistant)


class ChatSessionStoreTests(unittest.TestCase):
    def test_session_ids_are_opaque_unique_and_sessions_are_isolated(self):
        store = sessions.ChatSessionStore()
        first, second = store.create(), store.create()
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith('chat_'))
        complete_turn(store, first, 'one', 'answer one')
        self.assertEqual(store.snapshot(second).history, ())
        self.assertEqual(store.snapshot(first).history,
                         (sessions.Turn('one', 'answer one'),))

    def test_unknown_session_outcome_is_explicit(self):
        store = sessions.ChatSessionStore()
        for operation in (
                lambda: store.snapshot('missing'),
                lambda: store.begin('missing', 'hello'),
                lambda: store.complete('missing', 'turn', 'answer'),
                lambda: store.abort('missing', 'turn'),
                lambda: store.reset('missing')):
            with self.subTest(operation=operation):
                with self.assertRaises(sessions.UnknownSessionError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, 'unknown_session')

    def test_busy_session_rejects_second_begin_and_reset(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        begun = store.begin(identifier, 'first')
        with self.assertRaises(sessions.BusySessionError):
            store.begin(identifier, 'second')
        with self.assertRaises(sessions.BusySessionError):
            store.reset(identifier)
        self.assertTrue(store.snapshot(identifier).busy)
        store.abort(identifier, begun.turn_id)
        self.assertFalse(store.snapshot(identifier).busy)

    def test_concurrent_begin_permits_exactly_one_active_turn(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        barrier = Barrier(3)

        def begin(value):
            barrier.wait()
            try:
                return store.begin(identifier, value)
            except sessions.BusySessionError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(begin, value) for value in ('a', 'b')]
            barrier.wait()
            results = [future.result() for future in futures]
        begun = [item for item in results if isinstance(item, sessions.BeginResult)]
        rejected = [item for item in results if isinstance(item, sessions.BusySessionError)]
        self.assertEqual((len(begun), len(rejected)), (1, 1))
        store.abort(identifier, begun[0].turn_id)

    def test_concurrent_complete_and_abort_resolve_turn_exactly_once(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        begun = store.begin(identifier, 'one request')
        barrier = Barrier(3)

        def finish(operation):
            barrier.wait()
            try:
                operation()
                return 'resolved'
            except sessions.StaleTurnError:
                return 'stale'

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(finish, lambda: store.complete(
                    identifier, begun.turn_id, 'one answer')),
                executor.submit(finish, lambda: store.abort(
                    identifier, begun.turn_id)),
            ]
            barrier.wait()
            outcomes = [future.result() for future in futures]
        self.assertCountEqual(outcomes, ('resolved', 'stale'))
        snapshot = store.snapshot(identifier)
        self.assertFalse(snapshot.busy)
        self.assertIn(snapshot.history, ((), (sessions.Turn(
            'one request', 'one answer'),)))

    def test_abort_discards_turn_and_reset_clears_only_idle_history(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        first = store.begin(identifier, 'discard me')
        store.abort(identifier, first.turn_id)
        self.assertEqual(store.snapshot(identifier).history, ())
        completed = complete_turn(store, identifier, 'keep me', 'kept')
        self.assertEqual(store.snapshot(identifier).last_completion, completed)
        reset = store.reset(identifier)
        self.assertFalse(reset.busy)
        self.assertEqual(reset.history, ())
        self.assertIsNone(reset.last_completion)

    def test_stale_completion_and_abort_cannot_change_later_turn(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        old = store.begin(identifier, 'old')
        store.abort(identifier, old.turn_id)
        current = store.begin(identifier, 'current')
        with self.assertRaises(sessions.StaleTurnError):
            store.complete(identifier, old.turn_id, 'late answer')
        with self.assertRaises(sessions.StaleTurnError):
            store.abort(identifier, old.turn_id)
        self.assertTrue(store.snapshot(identifier).busy)
        store.complete(identifier, current.turn_id, 'current answer')
        self.assertEqual(store.snapshot(identifier).history,
                         (sessions.Turn('current', 'current answer'),))

    def test_history_accepts_only_nonempty_user_and_assistant_text(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        for invalid in ({'role': 'tool'}, ['telemetry'], None, '', '   '):
            with self.subTest(invalid=invalid):
                with self.assertRaises(sessions.InvalidMessageError):
                    store.begin(identifier, invalid)
        begun = store.begin(identifier, 'valid user')
        for invalid in ({'approval': 'approved'}, ['tool result'], None, '', '\n'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(sessions.InvalidMessageError):
                    store.complete(identifier, begun.turn_id, invalid)
                self.assertTrue(store.snapshot(identifier).busy)
        store.abort(identifier, begun.turn_id)

    def test_turn_count_evicts_whole_oldest_pairs(self):
        store = sessions.ChatSessionStore()
        identifier = store.create()
        for index in range(5):
            result = complete_turn(store, identifier, f'user {index}', f'answer {index}')
        self.assertEqual(result.retention, 'RETAINED')
        self.assertEqual(result.evicted_turns, 1)
        self.assertEqual(store.snapshot(identifier).history,
                         tuple(sessions.Turn(f'user {index}', f'answer {index}')
                               for index in range(1, 5)))

    def test_byte_limit_evicts_whole_pairs_deterministically(self):
        first = sessions.Turn('u1' * 8, 'a1' * 8)
        second = sessions.Turn('u2' * 8, 'a2' * 8)
        pair_bytes = len(json.dumps(
            [{'user': second.user, 'assistant': second.assistant}],
            ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        store = sessions.ChatSessionStore(max_history_bytes=pair_bytes + 3)
        identifier = store.create()
        complete_turn(store, identifier, first.user, first.assistant)
        result = complete_turn(store, identifier, second.user, second.assistant)
        self.assertEqual(result.evicted_turns, 1)
        self.assertEqual(store.snapshot(identifier).history, (second,))

    def test_multibyte_history_uses_utf8_bytes_not_character_count(self):
        turn = sessions.Turn('🙂漢字', 'é🙂')
        expected = len(json.dumps(
            [{'user': turn.user, 'assistant': turn.assistant}],
            ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        store = sessions.ChatSessionStore(max_history_bytes=expected)
        identifier = store.create()
        result = complete_turn(store, identifier, turn.user, turn.assistant)
        self.assertEqual(result.retention, 'RETAINED')
        self.assertEqual(result.history_bytes, expected)

    def test_oversized_new_pair_is_omitted_without_erasing_history(self):
        existing = sessions.Turn('short', 'kept')
        existing_bytes = len(json.dumps(
            [{'user': existing.user, 'assistant': existing.assistant}],
            ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        store = sessions.ChatSessionStore(max_history_bytes=existing_bytes + 8)
        identifier = store.create()
        complete_turn(store, identifier, existing.user, existing.assistant)
        result = complete_turn(store, identifier, 'large', 'x' * 200)
        self.assertEqual(result.retention, 'OMITTED_OVERSIZED')
        self.assertEqual(result.evicted_turns, 0)
        self.assertEqual(store.snapshot(identifier).history, (existing,))

    def test_capacity_evicts_least_recently_used_idle_session(self):
        store = sessions.ChatSessionStore(max_sessions=3)
        first, second, third = (store.create() for _ in range(3))
        active = store.begin(first, 'touch first')
        store.abort(first, active.turn_id)
        replacement = store.create()
        self.assertNotEqual(replacement, second)
        with self.assertRaises(sessions.UnknownSessionError):
            store.snapshot(second)
        self.assertEqual(store.snapshot(first).history, ())
        self.assertEqual(store.snapshot(third).history, ())

    def test_busy_sessions_are_not_evicted(self):
        store = sessions.ChatSessionStore(max_sessions=2)
        busy = store.create()
        idle = store.create()
        begun = store.begin(busy, 'active')
        store.create()
        self.assertTrue(store.snapshot(busy).busy)
        with self.assertRaises(sessions.UnknownSessionError):
            store.snapshot(idle)
        store.abort(busy, begun.turn_id)

    def test_creation_fails_when_every_capacity_slot_is_busy(self):
        store = sessions.ChatSessionStore(max_sessions=2)
        identifiers = [store.create(), store.create()]
        begun = [store.begin(identifier, 'active') for identifier in identifiers]
        with self.assertRaises(sessions.SessionCapacityError) as caught:
            store.create()
        self.assertEqual(caught.exception.code, 'session_capacity_exhausted')
        for identifier, turn in zip(identifiers, begun):
            store.abort(identifier, turn.turn_id)

    def test_configured_limits_cannot_exceed_contract_maxima(self):
        for arguments in (
                {'max_sessions': sessions.MAX_SESSIONS + 1},
                {'max_turns': sessions.MAX_TURNS + 1},
                {'max_history_bytes': sessions.MAX_HISTORY_BYTES + 1}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    sessions.ChatSessionStore(**arguments)


if __name__ == '__main__':
    unittest.main()
