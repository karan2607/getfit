"""
Next week is planned from the same engine the app suggests weights with.

`_week_history_summary` described each exercise from history pooled by NAME,
so the numbers reaching the week-generation prompt knew nothing about which
slot the movement sits in or what else is trained before it. The model was
asked to write next week's loading from a rounder, worse number than the one
the app shows the client on the day.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from api import views, weights, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

LB = 0.453592


class WeekHistoryUsesTheEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='nw@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1,
            duration_weeks=8)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Push')
        Exercise.objects.create(day=self.day, name='Barbell Bench Press',
                                sets=3, reps='8-12', order=0,
                                measurement_type=et.WEIGHT_REPS)
        self.row = Exercise.objects.create(
            day=self.day, name='Machine Chest Press', sets=3, reps='8-12',
            order=1, measurement_type=et.WEIGHT_REPS)
        for days_ago in (10, 3):
            s = WorkoutSession.objects.create(
                user=self.user, day_name='Upper Push', is_completed=True,
                completed_at=timezone.now() - timedelta(days=days_ago))
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.now() - timedelta(days=days_ago))
            for n in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=s, exercise=self.row,
                    exercise_name='Machine Chest Press', set_number=n,
                    weight_kg=100 * LB, reps_completed=12,
                    target_reps='8-12', is_completed=True)

    def test_the_summary_carries_the_engines_number(self):
        engine = weights.suggest_weight(self.user, self.row,
                                        target_reps='8-12', unit='lb')
        self.assertIsNotNone(engine['weight'])

        summary = views._week_history_summary(self.user, [self.row], 'lb')

        self.assertIn('Machine Chest Press', summary)
        self.assertIn(str(engine['weight']),
                      summary['Machine Chest Press'],
                      msg=summary['Machine Chest Press'])

    def test_the_trend_sentence_survives(self):
        """The prose the prompt already relied on is not replaced."""
        summary = views._week_history_summary(self.user, [self.row], 'lb')
        self.assertIn('→', summary['Machine Chest Press'])

    def test_an_exercise_with_no_history_is_left_out(self):
        fresh = Exercise.objects.create(
            day=self.day, name='Cable Fly', sets=3, reps='12-15', order=2,
            measurement_type=et.WEIGHT_REPS)
        summary = views._week_history_summary(self.user, [fresh], 'lb')
        self.assertNotIn('Cable Fly', summary)

    def test_bodyweight_work_gets_no_invented_load(self):
        row = Exercise.objects.create(
            day=self.day, name='Push-ups', sets=3, reps='10-15', order=3,
            measurement_type=et.BODYWEIGHT_REPS)
        for days_ago in (10, 3):
            s = WorkoutSession.objects.create(
                user=self.user, day_name='Upper Push', is_completed=True)
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.now() - timedelta(days=days_ago))
            for n in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=s, exercise=row, exercise_name='Push-ups',
                    set_number=n, reps_completed=12, target_reps='10-15',
                    is_completed=True)
        summary = views._week_history_summary(self.user, [row], 'lb')
        self.assertIn('Push-ups', summary)
        self.assertNotIn('lb', summary['Push-ups'])
