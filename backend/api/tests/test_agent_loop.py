"""
The tool-calling loop.

The previous chat had NO tools: it was told about a plan in its system prompt
and asked to describe it, which is why it could talk about a plan but never
read one, and why "what did I lift last week" was answered from a prompt blob
rather than from the database.

Mocked throughout. A suite that calls a paid API is slow, non-deterministic,
and makes CI need a key.
"""

import json
from unittest import mock

from django.test import TestCase

from api import agent_loop, agent_router, agent_tools, claude
from api.models import ToolCallLog, User, UserProfile


class _Text:
    type = 'text'

    def __init__(self, text):
        self.text = text


class _ToolUse:
    type = 'tool_use'

    def __init__(self, name, args, call_id='tu_1'):
        self.name = name
        self.input = args
        self.id = call_id


class _Usage:
    input_tokens = 10
    output_tokens = 20


class _Final:
    def __init__(self, content, stop_reason='end_turn'):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _Stream:
    """Mimics the SDK's streaming context manager."""

    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


def _client_yielding(*turns):
    """A client whose successive calls return the given (chunks, final)."""
    client = mock.MagicMock()
    client.messages.stream.side_effect = [
        _Stream(chunks, final) for chunks, final in turns]
    return client


class AgentLoopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='loop@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.state = agent_loop.AgentRun()

    def run_loop(self, client):
        """The turn's TEXT. Progress frames are collected separately."""
        self.statuses = []
        with mock.patch.object(claude, '_client', return_value=client):
            out = []
            for piece in agent_loop.run(
                    user=self.user, system_prompt='s',
                    history=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
                    domain=agent_router.WORKOUT, run_state=self.state):
                # A dict is a progress frame. It must never reach the saved
                # reply, which is exactly what joining it into the text would
                # do -- so the tests separate them the way the view does.
                if isinstance(piece, dict):
                    self.statuses.append(piece['status'])
                else:
                    out.append(piece)
            return ''.join(out)

    def test_a_plain_answer_streams_and_calls_no_tools(self):
        client = _client_yielding((['Hello ', 'there'],
                                   _Final([_Text('Hello there')])))
        self.assertEqual(self.run_loop(client), 'Hello there')
        self.assertEqual(self.state.tool_calls, [])
        self.assertEqual(self.state.rounds, 1)

    def test_a_tool_call_runs_and_the_loop_continues(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('get_profile', {})])),
            (['Your goal is fat loss'], _Final([_Text('Your goal is fat loss')])),
        )
        out = self.run_loop(client)
        self.assertIn('fat loss', out)
        self.assertEqual([c['tool'] for c in self.state.tool_calls], ['get_profile'])
        self.assertEqual(self.state.rounds, 2)
        self.assertTrue(ToolCallLog.objects.filter(tool='get_profile').exists())

    def test_all_results_go_back_in_one_message(self):
        """
        Splitting parallel results across several messages silently teaches
        the model to stop making parallel calls.
        """
        client = _client_yielding(
            ([], _Final([_ToolUse('get_profile', {}, 'a'),
                         _ToolUse('list_workout_plans', {}, 'b')])),
            (['done'], _Final([_Text('done')])),
        )
        self.run_loop(client)
        second_call = client.messages.stream.call_args_list[1]
        messages = second_call.kwargs['messages']
        tool_results = [m for m in messages
                        if isinstance(m.get('content'), list)
                        and m['content'] and isinstance(m['content'][0], dict)
                        and m['content'][0].get('type') == 'tool_result']
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(len(tool_results[0]['content']), 2)

    def test_a_failing_tool_is_returned_as_an_error_result(self):
        """Dropping a failed call teaches the model it succeeded."""
        client = _client_yielding(
            ([], _Final([_ToolUse('definitely_not_a_tool', {})])),
            (['sorry'], _Final([_Text('sorry')])),
        )
        self.run_loop(client)
        messages = client.messages.stream.call_args_list[1].kwargs['messages']
        result = messages[-1]['content'][0]
        self.assertTrue(result['is_error'])
        self.assertIn('unknown tool', result['content'])

    def test_it_stops_after_the_round_ceiling(self):
        """
        Each round is a billed request, so the ceiling is a cost control as
        much as a correctness one.
        """
        turns = [([], _Final([_ToolUse('get_profile', {})]))
                 for _ in range(agent_loop.MAX_ROUNDS)]
        out = self.run_loop(_client_yielding(*turns))
        self.assertTrue(self.state.stopped_early)
        self.assertEqual(self.state.rounds, agent_loop.MAX_ROUNDS)
        self.assertIn('stopped after several lookups', out)

    def test_running_out_of_rounds_says_so(self):
        """
        A turn that just stops looks identical to a crash from the user's
        side.
        """
        turns = [([], _Final([_ToolUse('get_profile', {})]))
                 for _ in range(agent_loop.MAX_ROUNDS)]
        self.assertNotEqual(self.run_loop(_client_yielding(*turns)).strip(), '')

    def test_a_refusal_raises_the_shared_error_type(self):
        client = _client_yielding(([], _Final([], stop_reason='refusal')))
        with mock.patch.object(claude, '_client', return_value=client), \
                self.assertRaises(claude.ClaudeError):
            list(agent_loop.run(
                user=self.user, system_prompt='s',
                history=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
                domain=agent_router.WORKOUT, run_state=self.state))

    def test_a_transport_failure_raises_claude_error(self):
        """The view turns this into an SSE `error` frame, not a 500."""
        client = mock.MagicMock()
        client.messages.stream.side_effect = RuntimeError('socket died')
        with mock.patch.object(claude, '_client', return_value=client), \
                self.assertRaises(claude.ClaudeError):
            list(agent_loop.run(
                user=self.user, system_prompt='s', history=[],
                domain=agent_router.WORKOUT, run_state=self.state))

    def test_only_workout_tools_are_offered_on_a_workout_turn(self):
        client = _client_yielding((['ok'], _Final([_Text('ok')])))
        self.run_loop(client)
        names = {t['name'] for t in client.messages.stream.call_args.kwargs['tools']}
        self.assertIn('log_set', names)
        self.assertNotIn('log_meal', names)

    def test_it_never_sends_temperature(self):
        client = _client_yielding((['ok'], _Final([_Text('ok')])))
        self.run_loop(client)
        kwargs = client.messages.stream.call_args.kwargs
        for banned in ('temperature', 'top_p', 'top_k'):
            self.assertNotIn(banned, kwargs)

    def test_an_oversized_tool_result_is_truncated(self):
        """
        Haiku reprices the WHOLE request above 100K tokens, so one runaway
        result must not drag the turn over the cliff.
        """
        big = {'blob': 'x' * (agent_loop.MAX_RESULT_CHARS * 2)}
        block = agent_loop._result_block('id', big, '')
        self.assertLessEqual(len(block['content']),
                             agent_loop.MAX_RESULT_CHARS + 32)
        self.assertIn('truncated', block['content'])


class ProposalSurfacingTests(TestCase):
    """
    A proposed plan change has to reach the client as DATA, not as prose.

    If the confirm card were built from the reply text, the user would be
    approving a paraphrase -- and the paraphrase is written by the same model
    whose plan edits dropped a calf raise. The draft is lifted out of the tool
    result so the card shows exactly what was recorded, and the change_id ties
    the confirmation to that exact draft.
    """

    def setUp(self):
        from api.models import Exercise, WorkoutDay, WorkoutPlan

        self.user = User.objects.create_user(email='prop@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=1)
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Push')
        Exercise.objects.create(
            day=day, name='Barbell Bench Press', sets=4, reps='6-10',
            order=0, measurement_type='weight_reps')
        self.state = agent_loop.AgentRun()

    def run_loop(self, client):
        self.statuses = []
        with mock.patch.object(claude, '_client', return_value=client):
            out = []
            for piece in agent_loop.run(
                    user=self.user, system_prompt='s',
                    history=[{'role': 'user', 'parts': [{'text': 'swap bench'}]}],
                    domain=agent_router.WORKOUT, run_state=self.state):
                if isinstance(piece, dict):
                    self.statuses.append(piece['status'])
                else:
                    out.append(piece)
            return ''.join(out)

    def test_a_proposal_is_lifted_onto_the_run(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('propose_plan_change', {
                'plan_id': str(self.plan.id),
                'changes': [{'day': 'Upper Push', 'name': 'Machine Chest Press'}],
            })])),
            (['Here is the change.'], _Final([_Text('Here is the change.')])),
        )
        self.run_loop(client)

        self.assertIsNotNone(self.state.proposed_change)
        self.assertTrue(self.state.proposed_change['change_id'])
        self.assertTrue(self.state.proposed_change['needs_confirmation'])
        self.assertFalse(self.state.proposed_change['applied'])

    def test_an_ordinary_turn_proposes_nothing(self):
        client = _client_yielding((['hello'], _Final([_Text('hello')])))
        self.run_loop(client)
        self.assertIsNone(self.state.proposed_change)

    def test_a_failed_proposal_is_not_surfaced_as_one(self):
        """An error result must not render as a confirmable draft."""
        client = _client_yielding(
            ([], _Final([_ToolUse('propose_plan_change', {
                'plan_id': str(self.plan.id), 'changes': [],
            })])),
            (['sorry'], _Final([_Text('sorry')])),
        )
        self.run_loop(client)
        self.assertIsNone(self.state.proposed_change)


class RepeatedToolCallTests(TestCase):
    """
    A repeat of a read already made this turn is served from the first
    result. Measured on staging: `list_workout_plans` ran twice in one turn,
    and because text only streams in the FINAL round, that repeat bought the
    user a whole extra round of blank screen.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='dedupe@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.state = agent_loop.AgentRun()

    def run_loop(self, client):
        self.statuses = []
        with mock.patch.object(claude, '_client', return_value=client):
            out = []
            for piece in agent_loop.run(
                    user=self.user, system_prompt='s',
                    history=[{'role': 'user', 'parts': [{'text': 'hi'}]}],
                    domain=agent_router.WORKOUT, run_state=self.state):
                if isinstance(piece, dict):
                    self.statuses.append(piece['status'])
                else:
                    out.append(piece)
            return ''.join(out)

    def test_an_identical_read_runs_once(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('get_profile', {}, 'a')])),
            ([], _Final([_ToolUse('get_profile', {}, 'b')])),
            (['done'], _Final([_Text('done')])),
        )
        with mock.patch.object(agent_tools, 'run_tool',
                               return_value=({'ok': True}, None)) as ran:
            self.run_loop(client)
        self.assertEqual(ran.call_count, 1, 'the repeat should not re-run')
        # Both calls are still REPORTED -- the model made two, and hiding one
        # would misrepresent the turn in ToolCallLog and the done payload.
        self.assertEqual([c['tool'] for c in self.state.tool_calls],
                         ['get_profile', 'get_profile'])

    def test_the_same_tool_with_different_arguments_is_not_a_repeat(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('get_plan_week', {'week_number': 1}, 'a')])),
            ([], _Final([_ToolUse('get_plan_week', {'week_number': 2}, 'b')])),
            (['done'], _Final([_Text('done')])),
        )
        with mock.patch.object(agent_tools, 'run_tool',
                               return_value=({'ok': True}, None)) as ran:
            self.run_loop(client)
        self.assertEqual(ran.call_count, 2)

    def test_a_write_is_never_served_from_the_cache(self):
        """
        Two identical proposals mean two drafts. Returning the first one
        quietly would hide that the model tried to propose again.
        """
        args = {'plan_id': 'p', 'changes': [{'name': 'X', 'action': 'add'}]}
        client = _client_yielding(
            ([], _Final([_ToolUse('propose_plan_change', args, 'a')])),
            ([], _Final([_ToolUse('propose_plan_change', args, 'b')])),
            (['done'], _Final([_Text('done')])),
        )
        with mock.patch.object(agent_tools, 'run_tool',
                               return_value=({'change_id': 'c1'}, None)) as ran:
            self.run_loop(client)
        self.assertEqual(ran.call_count, 2)

    def test_progress_is_reported_before_each_tool_runs(self):
        client = _client_yielding(
            ([], _Final([_ToolUse('get_plan_week', {'week_number': 3}, 'a')])),
            (['done'], _Final([_Text('done')])),
        )
        with mock.patch.object(agent_tools, 'run_tool',
                               return_value=({'ok': True}, None)):
            text = self.run_loop(client)
        self.assertEqual(self.statuses, ['Reading week 3 of your plan'])
        # And progress is NOT part of the answer -- the view saves the text,
        # so a status leaking in here would be saved as something the AI said.
        self.assertEqual(text, 'done')

    def test_an_unknown_tool_still_gets_an_honest_status(self):
        self.assertEqual(agent_loop.describe('some_new_tool', {}), 'some new tool')

    def test_a_missing_template_field_does_not_break_the_status(self):
        self.assertEqual(agent_loop.describe('get_plan_week', {}),
                         'Reading week')
