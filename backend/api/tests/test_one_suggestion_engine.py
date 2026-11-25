"""
The drawer and the session screen must give the same number.

`weights.suggest_weight` has been live all along, but only on the session
screen, through `suggest_for_session`. The exercise drawer computed its own
answer in `_build_exercise_advice` from history pooled by NAME -- no slot, no
fatigue discount, no day type. So the same lift showed two different numbers
depending on which screen you opened, and the more careful of the two was the
one nobody read while deciding what to load.

The engine is the answer. The drawer's prose is how it is said.
"""
from datetime import timedelta
import re

from django.test import TestCase
from django.utils import timezone

from api import views, weights, exercise_types as et
from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)

LB = 0.453592


def numbers_in(text):
    return [float(n) for n in re.findall(r'\d+(?:\.\d+)?', text or '')]


class BothScreensAgreeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='one@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Upper Push')
        # Two chest movements before it, so the fatigue discount is not zero
        # and a pooled-history answer cannot accidentally match.
        Exercise.objects.create(day=self.day, name='Barbell Bench Press',
                                sets=3, reps='8-12', order=0,
                                measurement_type=et.WEIGHT_REPS)
        Exercise.objects.create(day=self.day, name='Incline Dumbbell Press',
                                sets=3, reps='8-12', order=1,
                                measurement_type=et.WEIGHT_REPS)
        self.row = Exercise.objects.create(
            day=self.day, name='Machine Chest Press', sets=3, reps='8-12',
            order=2, measurement_type=et.WEIGHT_REPS)
        for days_ago in (10, 3):
            s = WorkoutSession.objects.create(
                user=self.user, day_name='Upper Push', is_completed=True,
                completed_at=timezone.now() - timedelta(days=days_ago))
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.now() - timedelta(days=days_ago))
            for n in range(1, 4):
                SetLog.objects.create(
                    workout_session=s, exercise=self.row,
                    exercise_name='Machine Chest Press', set_number=n,
                    weight_kg=100 * LB, reps_completed=12,
                    target_reps='8-12', is_completed=True)

    def test_the_drawer_quotes_the_engines_number(self):
        """A6: the guide advice is the engine's answer, not its own."""
        engine = weights.suggest_weight(self.user, self.row,
                                        target_reps='8-12', unit='lb')
        self.assertIsNotNone(engine['weight'])

        advice = views._build_exercise_advice(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS, '8-12', 'lb',
            exercise=self.row)

        self.assertIn(engine['weight'],
                      numbers_in(advice['recommended_weight']),
                      msg='drawer said %r, engine said %s'
                          % (advice['recommended_weight'], engine['weight']))

    def test_the_session_screen_and_the_drawer_match(self):
        """The same lift, the same day, two screens, one number."""
        session = WorkoutSession.objects.create(
            user=self.user, day_name='Upper Push', exercise_day=self.day)
        from_session = weights.suggest_for_session(session, unit='lb')
        screen = from_session[str(self.row.id)]['weight']

        advice = views._build_exercise_advice(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS, '8-12', 'lb',
            exercise=self.row)

        self.assertIn(screen, numbers_in(advice['recommended_weight']),
                      msg='session screen said %s, drawer said %r'
                          % (screen, advice['recommended_weight']))

    def test_the_slot_changes_the_drawers_answer(self):
        """
        Proof the drawer is really using the engine: the same movement in a
        fresh slot must get a different number. Pooled-by-name history
        cannot tell these apart -- that was the bug.
        """
        other = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Accessories')
        fresh = Exercise.objects.create(
            day=other, name='Machine Chest Press', sets=3, reps='8-12',
            order=0, measurement_type=et.WEIGHT_REPS)

        fatigued_advice = views._build_exercise_advice(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS, '8-12', 'lb',
            exercise=self.row)
        fresh_advice = views._build_exercise_advice(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS, '8-12', 'lb',
            exercise=fresh)

        self.assertNotEqual(numbers_in(fatigued_advice['recommended_weight']),
                            numbers_in(fresh_advice['recommended_weight']))

    def test_without_a_row_the_prose_path_still_answers(self):
        """Callers with only a name keep the history-based wording."""
        advice = views._build_exercise_advice(
            self.user, 'Machine Chest Press', et.WEIGHT_REPS, '8-12', 'lb')
        self.assertTrue(advice['recommended_weight'])
