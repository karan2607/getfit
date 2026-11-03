"""
The week inlined into the agent's system prompt.

Measured on staging before this existed: a one-exercise swap took 31.9s, of
which 30.7s passed before the first character, across three tool rounds --
two of them asking for the plan the user was already looking at. Text only
streams in the final round, so every lookup round is dead air.
"""
from django.test import TestCase

from api.models import Exercise, User, UserProfile, WorkoutDay, WorkoutPlan
from api.views import agent_week_context


class AgentWeekContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='wk@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='kg')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='Eight Weeks', is_active=True,
            duration_weeks=8, current_week=4,
            program_outline=[{'week': 4, 'phase': 'intensification'}])
        for week in (1, 4, 5):
            day = WorkoutDay.objects.create(
                plan=self.plan, day_number=1, week_number=week,
                name=f'Push W{week}')
            Exercise.objects.create(
                day=day, name=f'Bench Press W{week}', sets=4, reps='6-8',
                order=0, measurement_type='weight_reps')

    def test_it_inlines_the_current_week_by_default(self):
        text = agent_week_context(self.user, self.plan.id, 'swap my bench press')
        self.assertIn('Bench Press W4', text)

    def test_other_weeks_are_not_inlined(self):
        """The whole point: eight weeks of context became one."""
        text = agent_week_context(self.user, self.plan.id, 'swap my bench press')
        self.assertNotIn('Bench Press W1', text)
        self.assertNotIn('Bench Press W5', text)

    def test_a_week_named_in_the_request_wins_over_the_current_week(self):
        text = agent_week_context(self.user, self.plan.id, 'in week 5, drop the bench')
        self.assertIn('Bench Press W5', text)
        self.assertNotIn('Bench Press W4', text)

    def test_an_out_of_range_week_falls_back_rather_than_returning_nothing(self):
        text = agent_week_context(self.user, self.plan.id, 'what about week 99?')
        self.assertIn('Bench Press W4', text)

    def test_a_rep_range_is_not_read_as_a_week_number(self):
        """'8-12' must not select week 8."""
        text = agent_week_context(self.user, self.plan.id, 'make it 3 sets of 8-12')
        self.assertIn('Bench Press W4', text)

    def test_the_phase_map_travels_with_the_week(self):
        """
        Without it a week-local edit cannot tell accumulation from a deload,
        and will add volume to the week that exists to remove it.
        """
        text = agent_week_context(self.user, self.plan.id, 'add a set')
        self.assertIn('intensification', text)

    def test_it_tells_the_model_not_to_look_up_what_it_already_has(self):
        text = agent_week_context(self.user, self.plan.id, 'add a set')
        self.assertIn('get_plan_week', text)
        self.assertIn(str(self.plan.id), text)

    def test_no_plan_returns_nothing_so_the_caller_can_fall_back(self):
        self.assertEqual(agent_week_context(self.user, None, 'hello'), '')

    def test_another_users_plan_is_not_readable(self):
        other = User.objects.create_user(email='other@test.local', password='x')
        self.assertEqual(agent_week_context(other, self.plan.id, 'hello'), '')

    def test_a_week_with_no_days_returns_nothing(self):
        empty = WorkoutPlan.objects.create(
            user=self.user, title='Empty', duration_weeks=8, current_week=2)
        self.assertEqual(agent_week_context(self.user, empty.id, 'hi'), '')

    def test_it_is_far_smaller_than_the_whole_plan(self):
        """
        The cost claim, asserted rather than asserted-in-prose: one week of a
        three-week-populated plan must be a fraction of all of it.
        """
        from api.views import _build_system_prompt
        whole = _build_system_prompt(self.user, plan_id=self.plan.id)
        one_week = agent_week_context(self.user, self.plan.id, 'add a set')
        self.assertLess(len(one_week), len(whole))
        self.assertNotIn('Bench Press W1', one_week)
        self.assertIn('Bench Press W1', whole)
