"""
The exercise-history endpoint behind the progress modal.

Two defects, both found from one feedback item ("I don't see full scroll for
excercises in progress with a lot of sessions already done"):

1. The modal rendered `logs.slice(0, 30)` straight off this response, and the
   response is ordered OLDEST FIRST. With 42 logged sets of Dumbbell Lateral
   Raises across 14 sessions, that showed June through August and silently hid
   the four most recent sessions. Fixed in the UI by grouping newest-first; the
   ordering contract is pinned here so a future change cannot quietly break the
   assumption the UI now makes.

2. This endpoint still matched `exercise_name__iexact` after canonical naming
   shipped. `_exercise_history` was switched over; this one was missed, so the
   progress view was still splitting history by spelling while the coaching
   advice no longer was.
"""

import datetime as dt

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    SetLog,
)


class HistoryEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='hist@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Push')
        Exercise.objects.create(day=self.day, name='Incline Dumbbell Press',
                                sets=3, reps='8-12',
                                measurement_type=et.WEIGHT_REPS)

    def log(self, name, days_ago, weight_lb, reps, sets=3):
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push',
            is_completed=True)
        WorkoutSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - dt.timedelta(days=days_ago))
        for number in range(1, sets + 1):
            SetLog.objects.create(
                workout_session=session, exercise_name=name,
                set_number=number, weight_kg=weight_lb * 0.453592,
                reps_completed=reps, is_completed=True)

    def get(self, name='Incline Dumbbell Press'):
        res = self.client.get(reverse('workout-exercise-history', args=[name]))
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        return res.data

    # -- ordering ------------------------------------------------------------

    def test_logs_are_ordered_oldest_first(self):
        """
        The contract the UI depends on. It groups and reverses client-side, so
        if this ever flips the modal would silently show the wrong end again.
        """
        self.log('Incline Dumbbell Press', 30, 100, 8)
        self.log('Incline Dumbbell Press', 2, 120, 10)
        dates = [row['workout_session__started_at']
                 for row in self.get()['logs']]
        self.assertEqual(dates, sorted(dates))

    def test_the_full_history_is_returned_not_a_truncated_window(self):
        """
        14 sessions x 3 sets = 42 rows, the real production shape. The endpoint
        must return all of them so the UI can offer "All 14 sessions".
        """
        for index in range(14):
            self.log('Incline Dumbbell Press', 100 - index * 7, 100 + index, 10)
        data = self.get()
        self.assertEqual(len(data['logs']), 42)
        session_dates = {row['workout_session__started_at'].date()
                         for row in data['logs']}
        self.assertEqual(len(session_dates), 14)

    def test_the_most_recent_session_is_present(self):
        """What was invisible before: the newest sets must be in the payload."""
        for index in range(14):
            self.log('Incline Dumbbell Press', 100 - index * 7, 100 + index, 10)
        self.log('Incline Dumbbell Press', 0, 200, 12)
        weights = [row['weight_kg'] for row in self.get()['logs']]
        self.assertAlmostEqual(max(weights), 200 * 0.453592, places=3)

    # -- canonical names -----------------------------------------------------

    def test_history_spans_name_variants(self):
        """
        88 sets of "Incline Dumbbell Press" and 3 of "Dumbbell Incline Press"
        existed in production. The progress view must show one history.
        """
        self.log('Incline Dumbbell Press', 20, 100, 10)
        self.log('Dumbbell Incline Press', 10, 110, 10)
        self.assertEqual(len(self.get()['logs']), 6)

    def test_the_variant_name_resolves_to_the_same_history(self):
        self.log('Incline Dumbbell Press', 20, 100, 10)
        self.log('Dumbbell Incline Press', 10, 110, 10)
        self.assertEqual(len(self.get('Dumbbell Incline Press')['logs']), 6)

    def test_a_different_movement_is_not_merged_in(self):
        self.log('Incline Dumbbell Press', 20, 100, 10)
        self.log('Flat Dumbbell Press', 10, 110, 10)
        self.assertEqual(len(self.get()['logs']), 3)

    def test_the_measurement_type_resolves_across_variants(self):
        """
        A plank logged under a variant name must still report as duration, or
        the modal charts seconds as pounds.
        """
        Exercise.objects.create(day=self.day, name='Side Plank', sets=3,
                                reps='30 sec', measurement_type=et.DURATION,
                                order=2)
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push',
            is_completed=True)
        SetLog.objects.create(workout_session=session,
                              exercise_name='Side Planks', set_number=1,
                              duration_seconds=45, is_completed=True)
        data = self.get('Side Planks')
        self.assertEqual(data['measurement_type'], et.DURATION)
        self.assertEqual(data['metric'], 'duration_seconds')
        self.assertEqual(len(data['logs']), 1)

    # -- isolation -----------------------------------------------------------

    def test_another_users_history_is_not_returned(self):
        other = User.objects.create_user(email='hist2@test.local', password='x')
        session = WorkoutSession.objects.create(
            user=other, day_name='Theirs', is_completed=True)
        SetLog.objects.create(
            workout_session=session, exercise_name='Incline Dumbbell Press',
            set_number=1, weight_kg=99, reps_completed=5, is_completed=True)
        self.log('Incline Dumbbell Press', 5, 100, 10)
        data = self.get()
        self.assertEqual(len(data['logs']), 3)
        self.assertNotIn(99, [row['weight_kg'] for row in data['logs']])

    def test_incomplete_sets_are_excluded(self):
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=self.day, day_name='Push')
        SetLog.objects.create(
            workout_session=session, exercise_name='Incline Dumbbell Press',
            set_number=1, weight_kg=50, reps_completed=10, is_completed=False)
        self.assertEqual(self.get()['logs'], [])

    def test_an_unlogged_exercise_returns_an_empty_history(self):
        data = self.get('Barbell Overhead Press')
        self.assertEqual(data['logs'], [])
        self.assertEqual(data['exercise_name'], 'Barbell Overhead Press')

    def test_requires_auth(self):
        self.client.credentials()
        res = self.client.get(
            reverse('workout-exercise-history', args=['Incline Dumbbell Press']))
        self.assertIn(res.status_code, (401, 403))
