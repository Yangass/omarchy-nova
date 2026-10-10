import unittest
from unittest.mock import patch

import server


class Provider:
    def __init__(self, turns):
        self.turns = list(turns)

    def models(self):
        return [{'id': 'qwen3.5:35b', 'available': True}]

    def stream_chat(self, *_args):
        return self.turns.pop(0)


class CapturingProvider:
    def __init__(self):
        self.request = None

    def models(self):
        return [{'id': 'qwen3.5:35b', 'available': True}]

    def stream_chat(self, model, messages, tools, *_args):
        self.request = {'model': model, 'messages': messages, 'tools': tools}
        return {'role': 'assistant', 'content': 'What would you like to test?', 'tool_calls': []}


def call(name):
    return {'function': {'name': name, 'arguments': {}}}


class ChatSourceTests(unittest.TestCase):
    def run_turns(self, turns, result):
        original = server.PROVIDERS['ollama']
        original_provider, original_model = server.ACTIVE_PROVIDER, server.ACTIVE_MODEL
        server.PROVIDERS['ollama'] = Provider(turns)
        # This unit fixture supplies qwen3.5:35b.  Bind the request snapshot to
        # it instead of inheriting the user's configured live model.
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = 'ollama', 'qwen3.5:35b'
        sources = []
        try:
            with patch.object(server, 'tool_result', return_value=result), patch.object(server, 'utc_now', return_value='2026-09-10T14:32:00Z'):
                answer = server.run_agent_loop('test', on_data_source=sources.append)
        finally:
            server.PROVIDERS['ollama'] = original
            server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = original_provider, original_model
        return answer, sources

    def test_successful_read_only_tool_emits_broad_source_after_result(self):
        answer, sources = self.run_turns(
            [{'role': 'assistant', 'content': '', 'tool_calls': [call('get_gpu_usage')]},
             {'role': 'assistant', 'content': 'Explanation.', 'tool_calls': []}],
            ('get_gpu_usage', {'ok': True, 'data': {}}))
        self.assertEqual(answer, 'Explanation.')
        self.assertEqual(sources, [{'source': 'system telemetry', 'checked_at': '2026-09-10T14:32:00Z'}])

    def test_answer_without_tool_has_no_source(self):
        answer, sources = self.run_turns(
            [{'role': 'assistant', 'content': 'Explanation only.', 'tool_calls': []}],
            ('unused', {'ok': True}))
        self.assertEqual(answer, 'Explanation only.')
        self.assertEqual(sources, [])

    def test_failed_tool_result_emits_no_source(self):
        _answer, sources = self.run_turns(
            [{'role': 'assistant', 'content': '', 'tool_calls': [call('get_gpu_usage')]},
             {'role': 'assistant', 'content': 'No data.', 'tool_calls': []}],
            ('get_gpu_usage', {'ok': False, 'error': 'diagnostic_service_unavailable'}))
        self.assertEqual(sources, [])

    def test_repeated_tool_results_emit_the_actual_source_each_time_for_ui_deduplication(self):
        answer, sources = self.run_turns(
            [{'role': 'assistant', 'content': '', 'tool_calls': [call('get_cpu_usage')]},
             {'role': 'assistant', 'content': '', 'tool_calls': [call('get_gpu_usage')]},
             {'role': 'assistant', 'content': 'Explanation.', 'tool_calls': []}],
            ('get_cpu_usage', {'ok': True, 'data': {}}))
        self.assertEqual(answer, 'Explanation.')
        self.assertEqual([item['source'] for item in sources], ['system telemetry', 'system telemetry'])


class ChatInstructionContractTests(unittest.TestCase):
    def test_conversation_first_contract_reaches_provider_with_action_tools_unchanged(self):
        provider = CapturingProvider()
        original = server.PROVIDERS['ollama']
        original_provider, original_model = server.ACTIVE_PROVIDER, server.ACTIVE_MODEL
        server.PROVIDERS['ollama'] = provider
        server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = 'ollama', 'qwen3.5:35b'
        try:
            answer = server.run_agent_loop('test')
        finally:
            server.PROVIDERS['ollama'] = original
            server.ACTIVE_PROVIDER, server.ACTIVE_MODEL = original_provider, original_model

        self.assertEqual(answer, 'What would you like to test?')
        self.assertEqual(provider.request['model'], 'qwen3.5:35b')
        self.assertEqual(provider.request['messages'][1], {'role': 'user', 'content': 'test'})
        instructions = provider.request['messages'][0]
        self.assertEqual(instructions['role'], 'system')
        self.assertIn('Answer ordinary conversation, planning, and code-text questions directly.', instructions['content'])
        self.assertIn('does not by itself request command validation.', instructions['content'])
        self.assertIn('Discussing code or commands is distinct from asking NOVA to execute them.', instructions['content'])
        self.assertIn('only when the user asks NOVA to perform one available supported action.', instructions['content'])
        self.assertEqual(provider.request['tools'], server.MODEL_TOOLS)
        tool_names = {item['function']['name'] for item in provider.request['tools']}
        self.assertTrue(set(server.ACTION_REGISTRY).issubset(tool_names))


if __name__ == '__main__':
    unittest.main()
