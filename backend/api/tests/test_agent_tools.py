"""
The agent's tool surface, and its audit trail.

Built on `mcp.TOOLS` rather than beside it: that registry already holds a
description, a JSON Schema and a user-scoped handler for all 22 tools and is
covered by 87 tests. A second implementation would be a second set of scoping
bugs, and the two would drift the first time one gained a field.

What is worth pinning here is the DOMAIN SPLIT and the LOG, because both are
about preventing a wrong action rather than producing a right one.
"""

from unittest import mock

from django.test import TestCase

from api import agent_tools, mcp
from api.models import ToolCallLog, User, UserProfile


class DomainSplitTests(TestCase):
    """
    Each model sees only the tools it can act on.

    Not cosmetic: a model offered `log_meal` on a workout turn will
    eventually call it, and the cheapest way to prevent a wrong tool call is
    for the tool not to be on the list.
    """

    def test_workout_tools_exclude_meal_logging(self):
        names = agent_tools.tools_for(agent_tools.WORKOUT)
        self.assertIn('log_set', names)
        self.assertIn('get_exercise_history', names)
        self.assertNotIn('log_meal', names)
        self.assertNotIn('get_meal_logs', names)

    def test_food_tools_exclude_set_logging(self):
        names = agent_tools.tools_for(agent_tools.FOOD)
        self.assertIn('log_meal', names)
        self.assertIn('get_diet_plan', names)
        self.assertNotIn('log_set', names)
        self.assertNotIn('get_workout_plan', names)

    def test_the_profile_is_visible_to_both(self):
        """Goal, dietary preference and injuries bear on both sides."""
        for domain in (agent_tools.WORKOUT, agent_tools.FOOD):
            self.assertIn('get_profile', agent_tools.tools_for(domain))

    def test_every_mcp_tool_is_assigned_a_domain(self):
        """
        A tool added to MCP without a domain would be silently unreachable
        by the agent -- present, tested, and never offered to any model.
        """
        unassigned = sorted(set(mcp.TOOLS) - set(agent_tools._DOMAIN))
        self.assertEqual(unassigned, [])

    def test_no_domain_names_a_tool_that_does_not_exist(self):
        """
        "Exists" spans BOTH registries: the MCP tools, and the plan-mutation
        tools that are deliberately not in MCP because they need a draft the
        user confirms.
        """
        stale = sorted(name for name in agent_tools._DOMAIN
                       if agent_tools._spec(name) is None)
        self.assertEqual(stale, [])

    def test_every_plan_tool_is_assigned_a_domain(self):
        unassigned = sorted(set(agent_tools._PLAN_TOOL_SPECS)
                            - set(agent_tools._DOMAIN))
        self.assertEqual(unassigned, [])


class ToolSchemaTests(TestCase):
    def test_every_tool_is_strict(self):
        """
        `strict` with `additionalProperties: false` is what guarantees the
        arguments validate -- and it is how determinism is obtained now that
        `temperature` is rejected outright.
        """
        for tool in agent_tools.anthropic_tools(agent_tools.WORKOUT):
            self.assertTrue(tool['strict'], tool['name'])
            self.assertFalse(tool['input_schema']['additionalProperties'],
                             tool['name'])

    def test_descriptions_come_from_the_single_registry(self):
        for tool in agent_tools.anthropic_tools(agent_tools.FOOD):
            self.assertEqual(tool['description'],
                             mcp.TOOLS[tool['name']]['description'])


class RunToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tool@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')

    def test_a_successful_call_is_logged(self):
        result, error = agent_tools.run_tool(
            self.user, 'get_profile', {}, model='claude-haiku-5-5')
        self.assertEqual(error, '')
        self.assertIsNotNone(result)

        entry = ToolCallLog.objects.get()
        self.assertEqual(entry.tool, 'get_profile')
        self.assertTrue(entry.ok)
        self.assertEqual(entry.model, 'claude-haiku-5-5')
        self.assertIsNotNone(entry.duration_ms)

    def test_an_unknown_tool_is_an_error_not_an_exception(self):
        """
        The loop has to hand the model a tool_result either way. Dropping a
        failed call silently teaches it the call succeeded.
        """
        result, error = agent_tools.run_tool(self.user, 'definitely_not_a_tool', {})
        self.assertIsNone(result)
        self.assertIn('unknown tool', error)
        self.assertFalse(ToolCallLog.objects.get().ok)

    def test_a_handler_failure_is_captured_not_raised(self):
        with mock.patch.dict(mcp.TOOLS['get_profile'],
                             {'handler': mock.Mock(side_effect=RuntimeError('boom'))}):
            result, error = agent_tools.run_tool(self.user, 'get_profile', {})
        self.assertIsNone(result)
        self.assertIn('boom', error)
        entry = ToolCallLog.objects.get()
        self.assertFalse(entry.ok)
        self.assertIn('boom', entry.error)

    def test_arguments_are_recorded(self):
        """'The AI deleted my Tuesday' is unanswerable without them."""
        agent_tools.run_tool(self.user, 'get_exercise_history',
                             {'exercise_name': 'Barbell Bench Press'})
        self.assertEqual(ToolCallLog.objects.get().arguments,
                         {'exercise_name': 'Barbell Bench Press'})

    def test_the_result_is_summarised_not_stored_whole(self):
        """
        A full payload would duplicate the database into a log table and grow
        without bound.
        """
        agent_tools.run_tool(self.user, 'get_profile', {})
        summary = ToolCallLog.objects.get().result_summary
        self.assertLessEqual(len(summary), 500)

    def test_an_audit_failure_does_not_fail_the_call(self):
        with mock.patch('api.models.ToolCallLog.objects.create',
                        side_effect=RuntimeError('log is down')):
            result, error = agent_tools.run_tool(self.user, 'get_profile', {})
        self.assertEqual(error, '')
        self.assertIsNotNone(result)


class PlanMutationToolTests(TestCase):
    """
    The agent proposes; only the user's confirmation applies.

    These are deliberately NOT in `mcp.TOOLS`: that registry is read tools and
    narrow writes, and a plan edit needs a draft the user sees first.

    They are also what makes a week-scoped prompt safe. While chat edited
    plans by re-emitting the whole thing as JSON it had to be SHOWN the whole
    thing -- ~32,000 tokens in and the same back out to change one exercise,
    with every re-emitted day a chance to drop something. An agent that edits
    through a draft never reproduces the weeks it did not touch.
    """

    def setUp(self):
        from api.models import Exercise, WorkoutDay, WorkoutPlan

        self.user = User.objects.create_user(email='pt@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=3,
            current_week=2,
            program_outline=[{'start_week': 1, 'end_week': 2,
                              'name': 'Accumulation', 'focus': 'volume'},
                             {'start_week': 3, 'end_week': 3,
                              'name': 'Deload', 'focus': 'recover'}])
        for week in (1, 2, 3):
            day = WorkoutDay.objects.create(
                plan=self.plan, day_number=1, week_number=week,
                name='Upper Push', order=week)
            Exercise.objects.create(
                day=day, name='Barbell Bench Press', sets=4, reps='6-10',
                order=0, measurement_type='weight_reps')

    def test_get_plan_week_returns_one_week_not_the_plan(self):
        out, error = agent_tools.run_tool(
            self.user, 'get_plan_week',
            {'plan_id': str(self.plan.id), 'week_number': 2})
        self.assertEqual(error, '')
        self.assertEqual(out['week_number'], 2)
        self.assertEqual(len(out['days']), 1)
        self.assertEqual(out['weeks_total'], 3)

    def test_it_defaults_to_the_current_week(self):
        out, _ = agent_tools.run_tool(
            self.user, 'get_plan_week', {'plan_id': str(self.plan.id)})
        self.assertEqual(out['week_number'], 2)

    def test_the_phase_map_travels_with_the_week(self):
        """
        Without it a week-local edit cannot tell accumulation from a deload,
        and will happily add volume to the week that exists to remove it.
        """
        out, _ = agent_tools.run_tool(
            self.user, 'get_plan_week', {'plan_id': str(self.plan.id)})
        self.assertEqual([p['name'] for p in out['program_outline']],
                         ['Accumulation', 'Deload'])

    def test_another_users_plan_is_not_readable(self):
        other = User.objects.create_user(email='pt2@test.local', password='x')
        out, error = agent_tools.run_tool(
            other, 'get_plan_week', {'plan_id': str(self.plan.id)})
        self.assertIsNone(out)
        self.assertIn('not found', error)

    def test_propose_applies_nothing(self):
        from api.models import Exercise, PlanChange

        before = list(Exercise.objects.filter(day__plan=self.plan)
                      .values_list('name', flat=True))
        out, error = agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id),
             'changes': [{'day': 'Upper Push', 'name': 'Machine Chest Press'}],
             'keep_list': ['Barbell Bench Press']})

        self.assertEqual(error, '')
        self.assertTrue(out['needs_confirmation'])
        self.assertFalse(out['applied'])
        self.assertEqual(
            list(Exercise.objects.filter(day__plan=self.plan)
                 .values_list('name', flat=True)), before)
        self.assertEqual(PlanChange.objects.get().status, PlanChange.PROPOSED)

    def test_the_proposal_is_logged_like_any_tool_call(self):
        agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id),
             'changes': [{'day': 'Upper Push', 'name': 'Machine Chest Press'}]})
        entry = ToolCallLog.objects.get(tool='propose_plan_change')
        self.assertTrue(entry.ok)
        self.assertIn('Upper Push', json_dumps(entry.arguments))

    def test_an_empty_draft_is_refused(self):
        out, error = agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id), 'changes': []})
        self.assertIsNone(out)
        self.assertIn('non-empty', error)

    def test_plan_tools_are_workout_only(self):
        for name in agent_tools.PLAN_TOOLS:
            self.assertIn(name, agent_tools.tools_for(agent_tools.WORKOUT))
            self.assertNotIn(name, agent_tools.tools_for(agent_tools.FOOD))

    def test_plan_tools_are_strict_like_the_rest(self):
        by_name = {t['name']: t
                   for t in agent_tools.anthropic_tools(agent_tools.WORKOUT)}
        for name in agent_tools.PLAN_TOOLS:
            self.assertTrue(by_name[name]['strict'])
            self.assertFalse(by_name[name]['input_schema']['additionalProperties'])


def json_dumps(value):
    import json
    return json.dumps(value)
