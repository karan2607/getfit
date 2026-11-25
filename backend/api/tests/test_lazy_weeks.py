"""
Weeks are generated one at a time, so an edit to week N reaches week N+1.

This is the mechanism the owner asked for -- "allow weekly edits but next
week should have this context of our update before creating it. That saves
tokens" -- and the parts were already in place:

  * generation returns ONE week, so a new plan is created holding week 1
    and nothing else;
  * `_maybe_autoprepare_next_week` builds week N+1 once every non-rest day
    of week N has a completed session;
  * `_generate_week` reads `_previous_week_structure` from the LIVE rows, so
    whatever week N looks like when N+1 is built is what informs it;
  * `_start_week_preparation` refuses to regenerate a week that exists.

Nothing here changes that. These tests pin it, because it was never
asserted anywhere and the whole value of editing a week depends on the next
one not existing yet. Older plans that already hold every week are
untouched -- the refusal above is what protects them.
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api import views
from api.models import (
    Exercise, User, UserProfile, WorkoutDay, WorkoutPlan, WorkoutSession,
)

ONE_WEEK = {
    'title': 'P',
    'duration_weeks': 8,
    'week': [
        {'day_number': 1, 'name': 'Push', 'is_rest_day': False,
         'exercises': [{'name': 'Barbell Bench Press', 'sets': 3,
                        'reps': '8-12', 'measurement_type': 'weight_reps'}]},
    ],
}


class LazyWeekTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='lazy@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_a_new_plan_holds_only_the_first_week(self):
        res = self.client.post(reverse('workout-plans'), ONE_WEEK,
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        plan = WorkoutPlan.objects.get(pk=res.data['id'])
        self.assertEqual(plan.duration_weeks, 8)
        self.assertEqual(
            sorted(set(plan.days.values_list('week_number', flat=True))), [1])

    def test_the_next_week_is_built_from_the_week_as_it_now_stands(self):
        """
        An edit to week 1 reaches week 2 because week 2 does not exist yet
        and is read from the live rows when it is built.
        """
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1)
        day = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Push')
        Exercise.objects.create(day=day, name='Flat Dumbbell Press', sets=3,
                                reps='8-12', order=0,
                                measurement_type='weight_reps')

        previous = views._previous_week_structure(plan, 2)
        names = [e.name for d in previous for e in d['exercises']]
        self.assertEqual(names, ['Flat Dumbbell Press'])

    def test_a_week_that_already_exists_is_never_regenerated(self):
        """What protects an older plan that already holds all eight."""
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1)
        for week in (1, 2):
            WorkoutDay.objects.create(plan=plan, day_number=1,
                                      week_number=week, name='Push')

        started, reason = views._start_week_preparation(plan, 2)
        self.assertFalse(started)
        self.assertIn('already exists', reason)

    def test_the_next_week_starts_when_the_current_one_is_finished(self):
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            is_active=True)
        day = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Push')
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name='Push',
            is_completed=True)

        with mock.patch.object(views, '_start_week_preparation',
                               return_value=(True, 'started')) as start:
            views._maybe_autoprepare_next_week(session)
        start.assert_called_once()
        self.assertEqual(start.call_args[0][1], 2)

    def test_it_waits_while_a_day_of_this_week_is_unfinished(self):
        plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8, current_week=1,
            is_active=True)
        done = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Push')
        WorkoutDay.objects.create(
            plan=plan, day_number=2, week_number=1, name='Pull')
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=done, day_name='Push',
            is_completed=True)

        with mock.patch.object(views, '_start_week_preparation') as start:
            views._maybe_autoprepare_next_week(session)
        start.assert_not_called()
