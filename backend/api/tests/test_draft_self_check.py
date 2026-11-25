"""
The draft reports what it will do, before anyone confirms it.

The fourth restructure attempt worked because the owner pasted a
verification checklist into the chat -- exact row counts per day -- and the
model checked its own draft against the plan before presenting it. The three
attempts without that checklist did not.

A checklist that only works when a user remembers to paste it is not a
safeguard. `propose_plan_change` now answers with the day-by-day result of
the draft it just recorded: the same `build_payload` the apply path will
run, summarised. The model sees what its change produces before it speaks,
and a day that lost four exercises it never meant to touch is visible in the
tool result rather than in the plan afterwards.
"""
from django.test import TestCase

from api import agent_tools
from api.models import Exercise, User, WorkoutDay, WorkoutPlan


class DraftSelfCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='sc@test.local',
                                             password='x')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=4,
            current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        for order, name in enumerate(['Barbell Bench Press',
                                      'Machine Shoulder Press',
                                      'Cable Triceps Pushdown']):
            Exercise.objects.create(
                day=self.day, name=name, sets=3, reps='8-12', order=order,
                measurement_type='weight_reps')
        WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Pull')

    def propose(self, changes):
        return agent_tools.run_tool(
            self.user, 'propose_plan_change',
            {'plan_id': str(self.plan.id), 'changes': changes})

    def test_the_result_says_what_each_touched_day_will_hold(self):
        result, error = self.propose([
            {'day': 'Push', 'week': 1, 'action': 'replace',
             'name': 'Flat Dumbbell Press',
             'replaces': 'Barbell Bench Press'}])
        self.assertEqual(error, '', msg=error)

        after = result['after']
        push = next(d for d in after if d['day'] == 'Push')
        self.assertEqual(push['week'], 1)
        self.assertEqual(push['exercise_count'], 3)
        self.assertIn('Flat Dumbbell Press', push['exercises'])
        self.assertNotIn('Barbell Bench Press', push['exercises'])

    def test_a_day_losing_exercises_is_visible_before_confirming(self):
        """
        The Day-4 shape: a draft that removes more than it replaces shows
        the shrunken day in the tool result, where the model can still fix
        it.
        """
        result, error = self.propose([
            {'day': 'Push', 'week': 1, 'action': 'remove',
             'name': 'Machine Shoulder Press'},
            {'day': 'Push', 'week': 1, 'action': 'remove',
             'name': 'Cable Triceps Pushdown'}])
        self.assertEqual(error, '', msg=error)
        self.assertEqual(result['after'][0]['exercise_count'], 1)

    def test_the_whole_affected_week_is_reported(self):
        """
        Every day of a week the draft reaches, not only the days it names.

        The failure being caught is an exercise disappearing from a day
        NOBODY touched -- the calf raise that vanished in a v2 update. A
        preview of only the named days cannot see that. Names alone, so a
        week costs a few hundred characters rather than the 19,000 the
        agent exists to avoid re-reading.
        """
        result, _error = self.propose([
            {'day': 'Push', 'week': 1, 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertEqual(sorted(d['day'] for d in result['after']),
                         ['Pull', 'Push'])
        self.assertTrue(all(d['week'] == 1 for d in result['after']))

    def test_weeks_the_draft_does_not_reach_are_left_out(self):
        WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=2, name='Push')
        result, _error = self.propose([
            {'day': 'Push', 'week': 1, 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertEqual({d['week'] for d in result['after']}, {1})

    def test_a_programme_wide_change_reports_every_week_it_reaches(self):
        for week in (2, 3):
            day = WorkoutDay.objects.create(
                plan=self.plan, day_number=1, week_number=week, name='Push')
            Exercise.objects.create(
                day=day, name='Barbell Bench Press', sets=3, reps='8-12',
                order=0, measurement_type='weight_reps')

        result, error = self.propose([
            {'day': 'Push', 'week': 0, 'action': 'remove',
             'name': 'Barbell Bench Press'}])
        self.assertEqual(error, '', msg=error)
        self.assertEqual(sorted({d['week'] for d in result['after']}),
                         [1, 2, 3])

    def test_the_preview_never_writes_anything(self):
        before = list(Exercise.objects.filter(day=self.day)
                      .values_list('name', flat=True))
        self.propose([{'day': 'Push', 'week': 1, 'action': 'remove',
                       'name': 'Barbell Bench Press'}])
        after = list(Exercise.objects.filter(day=self.day)
                     .values_list('name', flat=True))
        self.assertEqual(before, after)

    def test_a_preview_failure_does_not_lose_the_draft(self):
        """
        The draft is the thing that matters. If summarising it fails, the
        user still gets a confirm button rather than an error.
        """
        from unittest import mock

        with mock.patch('api.plan_changes.build_payload',
                        side_effect=RuntimeError('boom')):
            result, error = self.propose([
                {'day': 'Push', 'week': 1, 'action': 'remove',
                 'name': 'Barbell Bench Press'}])
        self.assertEqual(error, '')
        self.assertTrue(result['change_id'])
        self.assertEqual(result['after'], [])
