"""
The progress table shows what was recommended, beside what was done.

Requested: "the progress table still doesn't show what the recommendation
was from the past entries." It showed what you did and what the plan asked
for, so "95 lb x 8, target 8-10" gave no way to tell whether the app said 95
and you matched it or said 110 and you missed by 15 -- which is the only
comparison that says whether the suggestion engine is any good.

The second test here is the important one. `views._session_data` carries a
warning that a suggestion written into a SetLog would become history the next
suggestion is computed from, which would turn a recommendation nobody lifted
into evidence that they did. Recording it is safe only while nothing reads it
back.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api import weights, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

LB = 0.453592


class RecordedSuggestionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='rec@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        self.row = Exercise.objects.create(
            day=self.day, name='Machine Chest Press', sets=3, reps='8-12',
            order=0, measurement_type=et.WEIGHT_REPS)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def history(self, lb, reps, days_ago):
        s = WorkoutSession.objects.create(
            user=self.user, day_name='Push', is_completed=True,
            completed_at=timezone.now() - timedelta(days=days_ago))
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for n in (1, 2, 3):
            SetLog.objects.create(
                workout_session=s, exercise=self.row,
                exercise_name='Machine Chest Press', set_number=n,
                weight_kg=lb * LB, reps_completed=reps, target_reps='8-12',
                is_completed=True)
        return s

    def start_session(self):
        res = self.client.post(reverse('workout-sessions'),
                               {'exercise_day_id': str(self.day.id)},
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['id']

    # -- A9 ---------------------------------------------------------------

    def test_a_new_set_records_what_was_recommended(self):
        self.history(100, 12, days_ago=7)
        expected = weights.suggest_weight(self.user, self.row,
                                          target_reps='8-12', unit='lb')
        self.assertIsNotNone(expected['weight'])

        session_id = self.start_session()
        logs = SetLog.objects.filter(workout_session_id=session_id,
                                     exercise=self.row)
        self.assertEqual(logs.count(), 3)
        for log in logs:
            self.assertAlmostEqual(log.suggested_weight_kg,
                                   expected['weight_kg'], places=2)

    def test_the_history_endpoint_carries_it(self):
        self.history(100, 12, days_ago=7)
        session_id = self.start_session()
        SetLog.objects.filter(workout_session_id=session_id).update(
            reps_completed=8, weight_kg=95 * LB, is_completed=True)

        res = self.client.get(
            reverse('workout-exercise-history',
                    args=['Machine Chest Press']))
        self.assertEqual(res.status_code, 200, res.data)
        recent = res.data['logs'][-1]
        self.assertIn('suggested_weight_kg', recent)
        self.assertIsNotNone(recent['suggested_weight_kg'])

    def test_a_set_logged_before_the_field_existed_reports_nothing(self):
        """Null, not a number recomputed from today's rules."""
        self.history(100, 12, days_ago=7)
        res = self.client.get(
            reverse('workout-exercise-history',
                    args=['Machine Chest Press']))
        self.assertIsNone(res.data['logs'][0]['suggested_weight_kg'])

    def test_an_exercise_with_no_history_records_no_suggestion(self):
        session_id = self.start_session()
        for log in SetLog.objects.filter(workout_session_id=session_id):
            self.assertIsNone(log.suggested_weight_kg)

    # -- A10: the line that must not be crossed ---------------------------

    def test_a_recommendation_never_becomes_history(self):
        """
        Suggest 110, lift 95. The next suggestion must be built from 95.

        A suggestion written into a SetLog and then read back would make a
        weight nobody lifted into evidence that they did, and the number
        would climb on its own every session.
        """
        self.history(100, 12, days_ago=14)
        session_id = self.start_session()
        SetLog.objects.filter(workout_session_id=session_id).update(
            suggested_weight_kg=110 * LB,
            weight_kg=95 * LB, reps_completed=10, is_completed=True)
        WorkoutSession.objects.filter(pk=session_id).update(
            is_completed=True,
            started_at=timezone.now() - timedelta(days=2))

        result = weights.suggest_weight(self.user, self.row,
                                        target_reps='8-12', unit='lb')
        self.assertIn('95', result['basis'],
                      msg='basis should be built from what was lifted: %r'
                          % result['basis'])
        self.assertNotIn('110', result['basis'])
        # And the number itself stays in the neighbourhood of 95, not 110.
        self.assertLess(result['weight'], 110)

    def test_the_pooled_history_reader_does_not_select_it(self):
        """`_exercise_history` feeds the trend analysis; keep it out."""
        from api import views

        self.history(100, 12, days_ago=7)
        logs, _metric = views._exercise_history(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS)
        self.assertTrue(logs)
        self.assertNotIn('suggested_weight_kg', logs[0])
