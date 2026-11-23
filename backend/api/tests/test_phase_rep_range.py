"""
Guide advice follows the plan across a training-phase change.

Reported: "Feels like plan generation is not taking week division switch like
moving from accumulation to intensification into account. The plan itself is
but guides are not."

The plan moved the rep range (accumulation 10-12 -> intensification 6-8), but
the guide judged every logged session against TODAY's range. 12 reps at 100 lb
from the accumulation block was scored against 6-8 as "topped the range, add
5 lb", and going the other way 8 heavy reps were scored against 12-15 as a
failure -- "DECREASE WEIGHT, you could not complete the target reps". Each set
records the target it was done against, so each session is now judged by its
own range, and a range change gets advice for the new range.
"""

import datetime as dt
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from api import views, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

KG_PER_LB = 0.453592


def session(day, weight_lb, reps_list, target):
    return [
        {
            'workout_session__started_at': dt.datetime(2026, 10, day, 10, 0),
            'set_number': i + 1,
            'weight_kg': weight_lb * KG_PER_LB,
            'reps_completed': reps,
            'duration_seconds': None,
            'target_reps': target,
        }
        for i, reps in enumerate(reps_list)
    ]


class PhaseChangeTests(APITestCase):
    def analyze(self, logs, target):
        return views._analyze_progression(
            logs, target_reps=views._target_rep_range(target),
            measurement_type=et.WEIGHT_REPS, unit='lb')

    def test_accumulation_to_intensification_goes_heavier(self):
        logs = (session(15, 100, [12, 12, 12], '10-12')
                + session(8, 100, [11, 11, 10], '10-12'))
        res = self.analyze(logs, '6-8')
        self.assertEqual(res['direction'], 'rerange')
        self.assertGreater(res['suggested_value'], 100)
        self.assertIn('New rep range 6-8 (was 10-12)', res['summary'])
        self.assertIn('HEAVIER', res['summary'])

    def test_intensification_to_accumulation_is_not_a_failure(self):
        """8 heavy reps on a 6-8 block is not 'short of 12-15'."""
        logs = (session(15, 150, [8, 8, 8], '6-8')
                + session(8, 145, [8, 8, 7], '6-8'))
        res = self.analyze(logs, '12-15')
        self.assertEqual(res['direction'], 'rerange')
        self.assertLess(res['suggested_value'], 150)
        self.assertNotIn('DECREASE WEIGHT', res['summary'])
        self.assertIn('LIGHTER', res['summary'])

    def test_old_phase_weights_are_not_proven_for_the_new_range(self):
        """
        One session into the new block and short of it: 'go back to' must
        not name a weight that was only ever lifted for a different range.
        """
        logs = (session(15, 120, [4, 4, 3], '6-8')
                + session(8, 100, [12, 12, 12], '10-12'))
        res = self.analyze(logs, '6-8')
        self.assertEqual(res['direction'], 'decrease')
        self.assertIsNone(res['best_at_target'])

    def test_unchanged_range_behaves_as_before(self):
        logs = (session(15, 100, [12, 12, 12], '10-12')
                + session(8, 100, [10, 10, 10], '10-12'))
        res = self.analyze(logs, '10-12')
        self.assertEqual(res['direction'], 'increase')

    def test_sets_without_a_recorded_target_use_todays_range(self):
        logs = (session(15, 100, [12, 12, 12], '')
                + session(8, 100, [12, 12, 12], ''))
        res = self.analyze(logs, '10-12')
        self.assertEqual(res['direction'], 'increase')


class GuideAdviceAcrossPhasesTests(TestCase):
    """End to end through the stored history the guide reads."""

    def setUp(self):
        self.user = User.objects.create_user(email='phase@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        plan = WorkoutPlan.objects.create(user=self.user, title='P', is_active=True)
        self.week1 = WorkoutDay.objects.create(
            plan=plan, day_number=1, week_number=1, name='Upper')
        self.old = Exercise.objects.create(
            day=self.week1, name='Barbell Bench Press', sets=3, reps='10-12',
            order=0, measurement_type='weight_reps')

    def log(self, days_ago, lb, reps, target='10-12'):
        s = WorkoutSession.objects.create(
            user=self.user, day_name='Upper', is_completed=True)
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for n, r in enumerate(reps, 1):
            SetLog.objects.create(
                workout_session=s, exercise=self.old,
                exercise_name='Barbell Bench Press', set_number=n,
                weight_kg=lb * KG_PER_LB, reps_completed=r,
                target_reps=target, is_completed=True)

    def test_guide_advises_for_the_new_phase(self):
        self.log(9, 135, [12, 12, 12])
        self.log(2, 135, [12, 12, 12])
        advice = views._build_exercise_advice(
            self.user, 'Barbell Bench Press', et.WEIGHT_REPS, '6-8', 'lb')
        self.assertIn('6-8', advice['recommended_weight'])
        self.assertIn('Go up to about', advice['recommended_weight'])
        self.assertIn('New rep range', advice['coaching_note'])

    def test_plan_row_reps_stand_in_for_a_missing_recorded_target(self):
        self.log(9, 135, [12, 12, 12], target='')
        self.log(2, 135, [12, 12, 12], target='')
        advice = views._build_exercise_advice(
            self.user, 'Barbell Bench Press', et.WEIGHT_REPS, '6-8', 'lb')
        self.assertIn('(was 10-12)', advice['coaching_note'])
