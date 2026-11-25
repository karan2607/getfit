"""
A plan edit means the week in front of you, unless you say otherwise.

Four attempts to restructure week 8 each flipped Days 2/3 in weeks 6 and 7
as well, and week 6's record is still wrong because of it.

The mechanism: a draft item with no `week` applies to that day in EVERY
week, and the tool description taught the model exactly that -- "Omit `week`
to change every week". For the HTTP endpoint that rule is right: a user who
asks to take bench press out of their programme means out of all of it. For
the agent it is not, because the agent is only ever shown ONE week
(`agent_week_context` inlines a single week), so "the week in front of it"
is the only scope it can see and the one it was silently not using.

The fix is to stop accepting silence. The model is told the week number in
its context block, so it can always say which week it means; a draft that
says neither is refused and corrected in the same turn, the way an invented
day name already is.
"""
from django.test import TestCase

from api import agent_tools, mcp
from api.models import (
    Exercise, PlanChange, User, WorkoutDay, WorkoutPlan,
)


class DraftScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='scope@test.local',
                                             password='x')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=8,
            current_week=8)
        for week in (6, 7, 8):
            for number, name in ((1, 'Push'), (2, 'Legs'), (3, 'Pull')):
                day = WorkoutDay.objects.create(
                    plan=self.plan, day_number=number, week_number=week,
                    name=name)
                Exercise.objects.create(
                    day=day, name='Barbell Bench Press', sets=3, reps='8-12',
                    order=0, measurement_type='weight_reps')

    def propose(self, changes):
        return agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id), 'changes': changes})

    def test_a_change_with_no_scope_is_refused(self):
        result, error = self.propose([
            {'day': 'Push', 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertIsNone(result)
        self.assertIn('week', error.lower())
        self.assertEqual(PlanChange.objects.count(), 0)

    def test_the_refusal_says_how_to_fix_it(self):
        _result, error = self.propose([
            {'day': 'Push', 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertIn('week 0', error)

    def test_the_schema_makes_silence_impossible(self):
        """
        The server check is a backstop. The real guard is the schema: the
        model cannot emit an item without a week, so the refusal above
        should never be reached in practice.
        """
        from api import agent_tools

        spec = [t for t in agent_tools.anthropic_tools('workout')
                if t['name'] == 'propose_plan_change'][0]
        item = spec['input_schema']['properties']['changes']['items']
        self.assertIn('week', item['required'])

    def test_a_week_scoped_change_is_recorded(self):
        result, error = self.propose([
            {'day': 'Push', 'week': 8, 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertEqual(error, '')
        self.assertEqual(result['draft'][0]['week'], 8)

    def test_week_zero_is_the_explicit_way_to_mean_the_programme(self):
        result, error = self.propose([
            {'day': 'Push', 'week': 0, 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertEqual(error, '')
        # Downstream, "every week" is still expressed as the ABSENCE of a
        # week -- `_matching_days` has not changed and nothing else has to
        # learn a new shape.
        self.assertIsNone(result['draft'][0].get('week'))

    def test_a_day_move_needs_a_scope_too(self):
        """
        This is the one that did the damage: a day moved without a week
        moves in every week, which is how fixing week 8 flipped 6 and 7.
        """
        result, error = self.propose([
            {'day': 'Legs', 'action': 'move_day', 'to_position': 3}])
        self.assertIsNone(result)
        self.assertIn('week', error.lower())

    def test_a_scoped_day_move_is_fine(self):
        _result, error = self.propose([
            {'day': 'Legs', 'week': 8, 'action': 'move_day',
             'to_position': 3}])
        self.assertEqual(error, '')

    def test_the_http_endpoint_keeps_its_own_meaning(self):
        """
        A user asking to take an exercise out of their programme means out
        of all of it. Only the agent, which sees one week, has to say.
        """
        from api.plan_changes import validate_draft

        validate_draft([{'day': 'Push', 'action': 'remove',
                         'name': 'Barbell Bench Press'}], plan=self.plan)
