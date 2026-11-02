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
        stale = sorted(set(agent_tools._DOMAIN) - set(mcp.TOOLS))
        self.assertEqual(stale, [])


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
