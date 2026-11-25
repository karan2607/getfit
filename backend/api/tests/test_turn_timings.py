"""
Where a turn's time goes.

"Prod chat is slow" has been answered twice with a guess. The numbers that
settle it are how long before the first character appears, how much of the
rest is the model and how much is tool calls, and how many tokens each round
carried. Each round's usage line sat in the log with no timing beside it and
no total anywhere.

Measurement only. Nothing here changes a knob -- the point is to have the
numbers before anyone does.
"""
from unittest import mock

from django.test import TestCase

from api import agent_loop, agent_router, claude
from api.models import User, UserProfile
from api.tests.test_agent_loop import (
    _Final, _Text, _ToolUse, _client_yielding,
)


class TurnTimingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tt@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.state = agent_loop.AgentRun()

    def drive(self, client):
        with mock.patch.object(claude, '_client', return_value=client):
            return [p for p in agent_loop.run(
                user=self.user, system_prompt='s',
                history=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
                domain=agent_router.WORKOUT, run_state=self.state)]

    def test_an_answered_turn_records_its_model_round(self):
        self.drive(_client_yielding(
            (['Hello ', 'there'], _Final([_Text('Hello there')]))))
        model = [t for t in self.state.timings if t['stage'] == 'model']
        self.assertEqual(len(model), 1)
        self.assertEqual(model[0]['in'], 10)
        self.assertEqual(model[0]['out'], 20)
        self.assertGreaterEqual(model[0]['ms'], 0)

    def test_time_to_first_text_is_recorded(self):
        self.drive(_client_yielding((['Hi'], _Final([_Text('Hi')]))))
        self.assertIsNotNone(self.state.first_text_ms)
        self.assertLessEqual(self.state.first_text_ms,
                             self.state.elapsed_ms())

    def test_a_turn_that_says_nothing_has_no_first_text(self):
        self.drive(_client_yielding(([], _Final([_Text('')]))))
        self.assertIsNone(self.state.first_text_ms)

    def test_a_tool_round_is_timed_separately_from_the_model(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('list_workout_plans', {})])),
            (['Done'], _Final([_Text('Done')])),
        )
        self.drive(client)
        stages = [t['stage'] for t in self.state.timings]
        self.assertEqual(stages.count('model'), 2)
        self.assertEqual(stages.count('tool'), 1)
        tool = next(t for t in self.state.timings if t['stage'] == 'tool')
        self.assertEqual(tool['tool'], 'list_workout_plans')
        self.assertFalse(tool['failed'])

    def test_the_summary_names_the_slowest_tool(self):
        self.state.model = 'claude-haiku-5-5'
        self.state.rounds = 2
        self.state.note('model', 1200, round=1, **{'in': 100, 'out': 20})
        self.state.note('tool', 30, tool='get_plan_week', failed=False)
        self.state.note('tool', 900, tool='get_exercise_history',
                        failed=False)
        self.state.first_text_ms = 1500

        line = self.state.summary()
        self.assertIn('rounds=2', line)
        self.assertIn('ttft=1500ms', line)
        self.assertIn('model=1200ms', line)
        self.assertIn('tools=930ms', line)
        self.assertIn('in=100', line)
        self.assertIn('slowest_tool=get_exercise_history/900ms', line)

    def test_the_summary_survives_a_turn_with_no_rounds(self):
        """A turn that failed before the first call still logs a line."""
        self.assertIn('rounds=0', agent_loop.AgentRun().summary())

    def test_it_is_logged_even_when_the_turn_fails(self):
        """
        A measurement that only lands on the happy path misses the turns
        worth measuring.
        """
        client = mock.MagicMock()
        client.messages.stream.side_effect = RuntimeError('boom')
        with self.assertLogs('api.agent_loop', level='INFO') as logs:
            with self.assertRaises(claude.ClaudeError):
                self.drive(client)
        self.assertTrue(any('agent turn' in line for line in logs.output),
                        logs.output)
