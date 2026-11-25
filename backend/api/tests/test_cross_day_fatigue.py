"""
Fatigue carries across days, not just down a session.

`fatigue_factor` counts exercises earlier in the SAME day. Chest trained hard
yesterday, or the same muscle on back-to-back days, was modelled nowhere: the
engine offered the same weight whether the last chest session was yesterday
or a fortnight ago.

Deliberately small. Recovery between sessions is the normal case and the
discount is a nudge, not a verdict -- a floor keeps it from turning a working
weight into a warm-up.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from api import weights, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

LB = 0.453592


class CrossDayFatigueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='xd@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True)
        self.today = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        self.row = Exercise.objects.create(
            day=self.today, name='Machine Chest Press', sets=3, reps='8-12',
            order=0, measurement_type=et.WEIGHT_REPS)
        # The history the suggestion is built from: a fortnight ago, clean.
        self.session('Push', self.row, 100, 12, days_ago=14)

    def session(self, day_name, row, lb, reps, days_ago, name=None):
        s = WorkoutSession.objects.create(
            user=self.user, day_name=day_name, is_completed=True,
            completed_at=timezone.now() - timedelta(days=days_ago))
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for n in (1, 2, 3):
            SetLog.objects.create(
                workout_session=s, exercise=row,
                exercise_name=name or row.name, set_number=n,
                weight_kg=lb * LB, reps_completed=reps, target_reps='8-12',
                is_completed=True)
        return s

    _next_day = 3

    def other_day_exercise(self, name, day_name='Upper'):
        CrossDayFatigueTests._next_day += 1
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=self._next_day, week_number=1,
            name=day_name)
        return Exercise.objects.create(
            day=day, name=name, sets=3, reps='8-12', order=0,
            measurement_type=et.WEIGHT_REPS)

    def suggest(self):
        return weights.suggest_weight(self.user, self.row,
                                      target_reps='8-12', unit='lb')['weight']

    def test_the_same_muscle_yesterday_pulls_the_number_down(self):
        rested = self.suggest()
        bench = self.other_day_exercise('Barbell Bench Press')
        self.session('Upper', bench, 135, 10, days_ago=1)
        self.assertLess(self.suggest(), rested)

    def test_a_different_muscle_yesterday_changes_nothing(self):
        rested = self.suggest()
        curl = self.other_day_exercise('Barbell Curl', day_name='Arms')
        self.session('Arms', curl, 60, 10, days_ago=1)
        self.assertEqual(self.suggest(), rested)

    def test_the_same_muscle_last_week_changes_nothing(self):
        rested = self.suggest()
        bench = self.other_day_exercise('Barbell Bench Press')
        self.session('Upper', bench, 135, 10, days_ago=6)
        self.assertEqual(self.suggest(), rested)

    def test_two_days_ago_counts_for_less_than_yesterday(self):
        bench = self.other_day_exercise('Barbell Bench Press')
        self.session('Upper', bench, 135, 10, days_ago=2)
        older = self.suggest()

        SetLog.objects.filter(exercise=bench).delete()
        WorkoutSession.objects.filter(day_name='Upper').delete()
        self.session('Upper', bench, 135, 10, days_ago=1)
        newer = self.suggest()

        self.assertLess(newer, older)

    def test_the_discount_has_a_floor(self):
        """A heavy week must not turn a working weight into a warm-up."""
        for i, name in enumerate(
                ['Barbell Bench Press', 'Incline Dumbbell Press',
                 'Cable Crossover', 'Dumbbell Flyes', 'Push-ups']):
            row = self.other_day_exercise(name, day_name='Upper %d' % i)
            self.session('Upper %d' % i, row, 100, 10, days_ago=1)
        self.assertGreaterEqual(self.suggest(), 100 * 0.80)

    def test_the_reason_is_stated(self):
        bench = self.other_day_exercise('Barbell Bench Press')
        self.session('Upper', bench, 135, 10, days_ago=1)
        basis = weights.suggest_weight(self.user, self.row,
                                       target_reps='8-12', unit='lb')['basis']
        self.assertIn('recent', basis.lower())

    def test_the_sessions_own_history_is_not_counted_against_itself(self):
        """
        The slot's own past session is the EVIDENCE for the number. Counting
        it as recent fatigue as well would discount every suggestion that
        had history at all.
        """
        self.session('Push', self.row, 100, 12, days_ago=1)
        basis = weights.suggest_weight(self.user, self.row,
                                       target_reps='8-12', unit='lb')['basis']
        self.assertNotIn('recent same-muscle', basis.lower())
