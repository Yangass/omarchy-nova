"""A thinking model keeps the chat stream alive (2026-10-22): the panel's chat timeout counts
from the last line novad sent, and a model can think for minutes without writing."""
import unittest
from unittest.mock import patch

import server


class ThinkingProvider:
    """Thinks in many small pieces (one every second of the patched clock), then answers."""

    def __init__(self, clock, pieces):
        self.clock, self.pieces = clock, pieces

    def models(self):
        return [{'id': 'qwen3:8b', 'available': True}]

    def stream_chat(self, _model, _messages, _tools, on_thinking, on_content):
        for _ in range(self.pieces):
            self.clock[0] += 1.0
            on_thinking()
        on_content('Done.')
        return {'role': 'assistant', 'content': 'Done.', 'tool_calls': []}


class ThinkingHeartbeatTests(unittest.TestCase):
    def run_loop(self, pieces, on_thinking):
        clock = [1000.0]
        original = server.PROVIDERS['ollama']
        selection = server.ACTIVE_PROVIDER, server.ACTIVE_MODEL
        server.PROVIDERS['ollama'] = ThinkingProvider(clock, pieces)
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = 'ollama', 'qwen3:8b'
        try:
            with patch.object(server.time, 'monotonic', side_effect=lambda: clock[0]):
                return server.run_agent_loop('think hard', on_thinking=on_thinking)
        finally:
            server.PROVIDERS['ollama'] = original
            server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = selection

    def test_long_thinking_sends_a_heartbeat_every_few_seconds_not_every_piece(self):
        beats = []
        self.assertEqual(self.run_loop(60, lambda: beats.append(1)), 'Done.')
        # 60 s of thinking at one heartbeat per 5 s: 12, never one per piece (60).
        self.assertEqual(len(beats), 60 // server.THINKING_HEARTBEAT_SECONDS)

    def test_thinking_without_a_listener_still_answers(self):
        self.assertEqual(self.run_loop(3, None), 'Done.')

    def test_the_heartbeat_comes_well_inside_the_panels_chat_timeout(self):
        # Panel.qml: xhr.timeout = 190000 with timeoutIsIdle; approvals wait 120 s.
        self.assertLess(server.THINKING_HEARTBEAT_SECONDS, 190 / 4)
        self.assertLess(server.APPROVAL_TTL_SECONDS, 190)


if __name__ == '__main__':
    unittest.main()
