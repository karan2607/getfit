"""
The Claude wrapper, tested without spending money.

Every test here mocks the SDK. Live calls are reserved for deliberate
verification runs, because a suite that hits a paid API is slow,
non-deterministic, and would make CI need a key.

The behaviours worth pinning are the ones that differ from Gemini and are
easy to get wrong: the stop reasons that are NOT errors in the HTTP sense,
and the fenced-JSON difference that would otherwise score Claude down for
punctuation in the eval.
"""

import json
from unittest import mock

from django.test import SimpleTestCase

from api import claude


class _Block:
    def __init__(self, text):
        self.type = 'text'
        self.text = text


class _Usage:
    def __init__(self, i=10, o=20):
        self.input_tokens = i
        self.output_tokens = o


class _Message:
    def __init__(self, text, stop_reason='end_turn', usage=None):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()


def _mock_client(message):
    client = mock.MagicMock()
    client.messages.create.return_value = message
    return mock.patch.object(claude, '_client', return_value=client), client


class CallClaudeJsonTests(SimpleTestCase):
    def test_it_parses_a_plain_json_reply(self):
        patch, _ = _mock_client(_Message('{"title": "P", "week": []}'))
        with patch:
            out = claude.call_claude_json(system_prompt='s', user_prompt='u')
        self.assertEqual(out['title'], 'P')

    def test_it_unwraps_a_markdown_fence(self):
        """
        Gemini is told response_mime_type=application/json and cannot fence;
        Claude has no equivalent on a plain call. Without this the eval would
        score Claude down for punctuation rather than programming.
        """
        patch, _ = _mock_client(_Message('```json\n{"title": "P"}\n```'))
        with patch:
            out = claude.call_claude_json(system_prompt='s', user_prompt='u')
        self.assertEqual(out['title'], 'P')

    def test_it_never_sends_temperature(self):
        """Haiku 5.5 rejects non-default temperature with a 400."""
        patch, client = _mock_client(_Message('{}'))
        with patch:
            claude.call_claude_json(system_prompt='s', user_prompt='u')
        kwargs = client.messages.create.call_args.kwargs
        for banned in ('temperature', 'top_p', 'top_k'):
            self.assertNotIn(banned, kwargs)

    def test_it_sends_an_effort_level(self):
        patch, client = _mock_client(_Message('{}'))
        with patch:
            claude.call_claude_json(system_prompt='s', user_prompt='u')
        self.assertEqual(
            client.messages.create.call_args.kwargs['output_config'],
            {'effort': 'low'})

    def test_a_truncated_reply_says_so(self):
        """
        Truncated JSON parses as garbage. Reporting it as a malformed
        response would send someone hunting the wrong bug.
        """
        patch, _ = _mock_client(_Message('{"title": "P"', stop_reason='max_tokens'))
        with patch, self.assertRaises(claude.ClaudeError) as ctx:
            claude.call_claude_json(system_prompt='s', user_prompt='u')
        self.assertIn('cut off', str(ctx.exception))

    def test_a_refusal_is_reported_as_one(self):
        patch, _ = _mock_client(_Message('', stop_reason='refusal'))
        with patch, self.assertRaises(claude.ClaudeError) as ctx:
            claude.call_claude_json(system_prompt='s', user_prompt='u')
        self.assertIn('declined', str(ctx.exception))

    def test_non_json_raises_the_shared_error_type(self):
        """Call sites catch one shape regardless of provider."""
        patch, _ = _mock_client(_Message('I cannot do that'))
        with patch, self.assertRaises(claude.ClaudeError):
            claude.call_claude_json(system_prompt='s', user_prompt='u')

    def test_the_model_is_overridable(self):
        patch, client = _mock_client(_Message('{}'))
        with patch:
            claude.call_claude_json(system_prompt='s', user_prompt='u',
                                    model='claude-sonnet-5-5')
        self.assertEqual(client.messages.create.call_args.kwargs['model'],
                         'claude-sonnet-5-5')

    def test_the_default_model_is_haiku(self):
        self.assertEqual(claude._model(), 'claude-haiku-5-5')


class UsageLoggingTests(SimpleTestCase):
    def test_it_logs_tokens_and_cost(self):
        with self.assertLogs('api.claude', level='INFO') as captured:
            claude._log_usage('messages.create', 'claude-haiku-5-5',
                              _Usage(1000000, 0))
        line = captured.output[0]
        self.assertIn('in=1000000', line)
        self.assertIn('$0.10', line)

    def test_logging_never_raises(self):
        claude._log_usage('messages.create', 'claude-haiku-5-5', None)
        claude._log_usage('messages.create', 'unknown-model', _Usage())
