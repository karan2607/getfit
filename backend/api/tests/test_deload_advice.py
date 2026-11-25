"""
Advice must not contradict the plan it is advising on.

Live, 23 Nov 2026, mid-workout. Week 8 is a deload. Day 1 prescribes
Push-ups 3x5. The exercise drawer said "Improved to 12 reps from 10 reps ->
KEEP PROGRESSING" and "Go for 14 reps -- you hit 12", and the owner had to
stop and ask which number to follow.

The mechanism: `_build_exercise_advice` was called with an exercise NAME, so
it never knew the week, the phase or the slot, and could not tell a deload
from any other week. The prescribed 5 was parsed and then never used -- the
rep branch hardcoded `hit_all = topped = True`, so the target gated nothing,
and the bodyweight increase branch always said `last + 2`.

The rule, in the owner's words: "the suggestion is a function of the plan row
first (phase, prescribed target, slot), history second."
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api import views, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

KG_PER_LB = 0.453592

#: Eight weeks, the last of which is the deload that produced the bug.
OUTLINE = [
    {'name': 'Accumulation', 'start_week': 1, 'end_week': 4,
     'focus': 'volume'},
    {'name': 'Intensification', 'start_week': 5, 'end_week': 7,
     'focus': 'load'},
    {'name': 'Deload', 'start_week': 8, 'end_week': 8, 'focus': 'recovery'},
]


class DeloadAdviceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='deload@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=8,
            current_week=8, program_outline=OUTLINE)
        self.week7 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=7, name='Push')
        self.week8 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=8, name='Push')

    def exercise(self, day, name, reps, mtype=et.BODYWEIGHT_REPS, order=0):
        return Exercise.objects.create(
            day=day, name=name, sets=3, reps=reps, order=order,
            measurement_type=mtype)

    def log(self, row, days_ago, reps, lb=None, target=''):
        s = WorkoutSession.objects.create(
            user=self.user, day_name=row.day.name, is_completed=True)
        WorkoutSession.objects.filter(pk=s.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for n, r in enumerate(reps, 1):
            SetLog.objects.create(
                workout_session=s, exercise=row, exercise_name=row.name,
                set_number=n, reps_completed=r,
                weight_kg=(lb * KG_PER_LB) if lb else None,
                target_reps=target or row.reps, is_completed=True)

    # -- the reported bug ---------------------------------------------------

    def test_a_deload_week_does_not_tell_you_to_go_for_more(self):
        """THE live case: prescribed 5, history 12, advice said 14."""
        hard = self.exercise(self.week7, 'Push-ups', '10-12')
        self.log(hard, 9, [10, 10, 10])
        self.log(hard, 2, [12, 12, 12])
        deload = self.exercise(self.week8, 'Push-ups', '5')

        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '5', 'lb',
            exercise=deload)

        target = advice['recommended_target'] or ''
        self.assertIn('5', target)
        self.assertNotIn('14', target)
        self.assertNotIn('Go for', target)

    def test_no_advice_field_urges_more_in_a_deload_week(self):
        """A7: nothing anywhere on a deload day says increase."""
        hard = self.exercise(self.week7, 'Push-ups', '10-12')
        self.log(hard, 9, [10, 10, 10])
        self.log(hard, 2, [12, 12, 12])
        deload = self.exercise(self.week8, 'Push-ups', '5')

        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '5', 'lb',
            exercise=deload)

        blob = ' '.join(v for v in advice.values() if v).lower()
        for word in ('increase', 'go for more', 'go up', 'keep progressing'):
            self.assertNotIn(word, blob)

    def test_the_weighted_path_respects_a_deload_too(self):
        hard = self.exercise(self.week7, 'Barbell Bench Press', '8-10',
                             mtype=et.WEIGHT_REPS)
        self.log(hard, 9, [10, 10, 10], lb=135)
        self.log(hard, 2, [10, 10, 10], lb=135)
        deload = self.exercise(self.week8, 'Barbell Bench Press', '5',
                               mtype=et.WEIGHT_REPS)

        advice = views._build_exercise_advice(
            self.user, 'Barbell Bench Press', et.WEIGHT_REPS, '5', 'lb',
            exercise=deload)

        self.assertNotIn('Go up', advice['recommended_weight'] or '')

    # -- the target is a cap, deload or not ---------------------------------

    def test_the_prescribed_top_caps_a_bodyweight_increase(self):
        """
        Independent of any phase: history of 12 against a prescribed 8-10
        must not propose 14. The old branch always said last + 2.
        """
        row = self.exercise(self.week7, 'Push-ups', '8-10')
        self.log(row, 9, [10, 10, 10])
        self.log(row, 2, [12, 12, 12])

        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '8-10', 'lb',
            exercise=row)

        self.assertNotIn('14', advice['recommended_target'] or '')

    def test_the_cap_protects_a_plan_with_no_phase_map_at_all(self):
        """
        The phase map is the nice version of this fix, and it is not always
        there: the production probe account's own 8-week plan has an empty
        `program_outline`, either dropped by validation or predating the
        field. `is_deload` can never fire for a plan like that.

        What protects it is the prescription itself. Prescribed 5 against a
        12-rep history reads as "already at target" with no phase involved,
        which is the reported case. This test exists so that stays true if
        the phase handling is ever changed.
        """
        bare = WorkoutPlan.objects.create(
            user=self.user, title='No outline', duration_weeks=8,
            current_week=8, program_outline=[])
        day = WorkoutDay.objects.create(
            plan=bare, day_number=1, week_number=8, name='Push')
        hard = Exercise.objects.create(
            day=day, name='Push-ups', sets=3, reps='10-12', order=0,
            measurement_type=et.BODYWEIGHT_REPS)
        self.log(hard, 9, [10, 10, 10])
        self.log(hard, 2, [12, 12, 12])
        cut = Exercise.objects.create(
            day=day, name='Push-ups', sets=3, reps='5', order=1,
            measurement_type=et.BODYWEIGHT_REPS)

        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '5', 'lb',
            exercise=cut)

        target = advice['recommended_target'] or ''
        self.assertIn('5', target)
        self.assertNotIn('14', target)

    def test_a_normal_week_still_progresses(self):
        """The fix must not flatten ordinary progression."""
        row = self.exercise(self.week7, 'Push-ups', '10-15')
        self.log(row, 9, [10, 10, 10])
        self.log(row, 2, [12, 12, 12])

        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '10-15', 'lb',
            exercise=row)

        self.assertIn('14', advice['recommended_target'] or '')

    def test_advice_without_a_plan_row_still_works(self):
        """Callers that have only a name keep working."""
        row = self.exercise(self.week7, 'Push-ups', '10-15')
        self.log(row, 9, [10, 10, 10])
        self.log(row, 2, [12, 12, 12])
        advice = views._build_exercise_advice(
            self.user, 'Push-ups', et.BODYWEIGHT_REPS, '10-15', 'lb')
        self.assertIsNotNone(advice['recommended_target'])


class DeloadGuideEndpointTests(TestCase):
    """End to end: the drawer is what the owner was reading."""

    def setUp(self):
        self.user = User.objects.create_user(email='deload2@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, duration_weeks=8,
            current_week=8, program_outline=OUTLINE)
        week7 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=7, name='Push')
        week8 = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=8, name='Push')
        self.hard = Exercise.objects.create(
            day=week7, name='Push-ups', sets=3, reps='10-12', order=0,
            measurement_type=et.BODYWEIGHT_REPS)
        self.deload = Exercise.objects.create(
            day=week8, name='Push-ups', sets=3, reps='5', order=0,
            measurement_type=et.BODYWEIGHT_REPS)
        for days_ago, reps in ((9, 10), (2, 12)):
            s = WorkoutSession.objects.create(
                user=self.user, day_name='Push', is_completed=True)
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.now() - timedelta(days=days_ago))
            for n in range(1, 4):
                SetLog.objects.create(
                    workout_session=s, exercise=self.hard,
                    exercise_name='Push-ups', set_number=n,
                    reps_completed=reps, target_reps='10-12',
                    is_completed=True)

    def test_the_drawer_shows_the_deload_number(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.get(reverse('exercise-guide'),
                         {'name': 'Push-ups',
                          'exercise_id': str(self.deload.id)})
        self.assertEqual(res.status_code, 200, res.data)
        target = res.data.get('recommended_target') or ''
        self.assertIn('5', target)
        self.assertNotIn('14', target)
