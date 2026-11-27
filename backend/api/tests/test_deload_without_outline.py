"""
A deload week that no phase map announces.

Reported 26 Nov, after the phase-aware fix shipped: "Suggested is still
wrong I feel. I am being asked to increase lbs in a de-load week."

The earlier fix reads `program_outline`, and the owner's plan has an empty
one -- confirmed on the production probe account, whose generated 8-week
plan also carries no phases. So `is_deload` can never fire for it.

The rep CAP still protected the bodyweight advice, which is why the first
report was about reps and this one is about load. For a weighted lift the
cap does the opposite: a target cut from 12 reps to 5 reads as "you can go
heavier for fewer reps", which is correct for intensification and exactly
wrong for a deload. Nothing in the prescription alone distinguishes the
two.

What does distinguish them is the PLAN. A deload cuts work: fewer sets, or
fewer reps, or both, against the week before. That is in the rows already,
so it needs no outline and no new field.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from api import views, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

KG_PER_LB = 0.453592


class DeloadWithoutAnOutlineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dl@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=8,
            current_week=8, program_outline=[])

    def week(self, number, sets, reps):
        day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=number, name='Push')
        return Exercise.objects.create(
            day=day, name='Machine Chest Press', sets=sets, reps=reps,
            order=0, measurement_type=et.WEIGHT_REPS)

    def log(self, row, days_ago, lb, reps):
        s = WorkoutSession.objects.create(
            user=self.user, day_name='Push', is_completed=True,
            completed_at=timezone.now() - timedelta(days=days_ago))
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for n in range(1, row.sets + 1):
            SetLog.objects.create(
                workout_session=s, exercise=row, exercise_name=row.name,
                set_number=n, weight_kg=lb * KG_PER_LB, reps_completed=reps,
                target_reps=row.reps, is_completed=True)

    # -- the reported case -------------------------------------------------

    def test_a_week_that_cuts_the_work_is_treated_as_a_deload(self):
        hard = self.week(7, sets=4, reps='10-12')
        self.log(hard, 10, 100, 12)
        self.log(hard, 3, 100, 12)
        easy = self.week(8, sets=2, reps='5')

        self.assertTrue(views.looks_like_a_deload(self.plan, 8))

        advice = views._build_exercise_advice(
            self.user, hard.name, et.WEIGHT_REPS, '5', 'lb', exercise=easy)
        self.assertNotIn('Go up', advice['recommended_weight'] or '')
        self.assertIn('Deload', advice['recommended_weight'] or '')

    def test_an_ordinary_week_is_not_mistaken_for_one(self):
        """Intensification also cuts reps -- and ADDS load. Not a deload."""
        acc = self.week(7, sets=4, reps='10-12')
        self.log(acc, 10, 100, 12)
        self.log(acc, 3, 100, 12)
        intens = self.week(8, sets=5, reps='5-8')

        self.assertFalse(views.looks_like_a_deload(self.plan, 8))
        advice = views._build_exercise_advice(
            self.user, acc.name, et.WEIGHT_REPS, '5-8', 'lb', exercise=intens)
        self.assertNotIn('Deload', advice['recommended_weight'] or '')

    def test_the_first_week_is_never_a_deload(self):
        """There is nothing before it to be lighter than."""
        self.week(1, sets=3, reps='8-12')
        self.assertFalse(views.looks_like_a_deload(self.plan, 1))

    def test_a_missing_previous_week_is_not_a_deload(self):
        self.week(8, sets=2, reps='5')
        self.assertFalse(views.looks_like_a_deload(self.plan, 8))

    def test_the_phase_map_still_wins_when_there_is_one(self):
        """An outline that says deload is believed without counting sets."""
        self.plan.program_outline = [
            {'name': 'Deload', 'start_week': 8, 'end_week': 8}]
        self.plan.save(update_fields=['program_outline'])
        self.week(7, sets=3, reps='8-12')
        self.week(8, sets=3, reps='8-12')      # same volume
        self.assertTrue(views.looks_like_a_deload(self.plan, 8))
