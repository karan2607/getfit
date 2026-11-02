"""
Suggested weight, per exercise INSTANCE.

The problem being solved is the blanket last-weight. `_exercise_history`
pools every logged set for a name, so "what should I use for machine chest
press?" gets one answer regardless of where it sits in the session. The same
movement as the LEAD press on a push day and as the third chest movement on
an accessories day are different jobs; one number is too heavy in one place
and too light in the other, and wrong in both.

The headline test is the acceptance case stated when this was specified:

    machine press history 100 lb x 12 fatigued vs 120 lb x 15 fresh
    -> next push day suggests ~100, next accessories day ~120
    -> a 15-rep target suggests BELOW the 12-rep suggestion
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from api.models import (
    Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
    WorkoutSession,
)
from api.weights import fatigue_factor, suggest_weight

LB = 0.453592


class SuggestWeightTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='w@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True)

    # -- fixture helpers --------------------------------------------------

    def day(self, name, number):
        return WorkoutDay.objects.create(
            plan=self.plan, day_number=number, week_number=1, name=name)

    def exercise(self, day, name, order, reps='8-12'):
        return Exercise.objects.create(
            day=day, name=name, sets=3, reps=reps, order=order,
            measurement_type='weight_reps')

    def log(self, day_name, exercise_name, lb, reps, days_ago=7):
        session = WorkoutSession.objects.create(
            user=self.user, day_name=day_name, is_completed=True,
            completed_at=timezone.now() - timedelta(days=days_ago))
        WorkoutSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        session.refresh_from_db()
        SetLog.objects.create(
            workout_session=session, exercise_name=exercise_name,
            set_number=1, weight_kg=lb * LB, reps_completed=reps,
            is_completed=True)
        return session

    # -- the acceptance case ---------------------------------------------

    def test_the_same_movement_in_two_slots_gets_two_answers(self):
        """
        THE case this engine exists for. Identical exercise name, different
        slot, different honest answer.
        """
        push = self.day('Upper Push', 1)
        accessories = self.day('Accessories', 2)

        # Fatigued: third chest movement on push day, 100 lb x 12.
        self.exercise(push, 'Barbell Bench Press', 0)
        self.exercise(push, 'Incline Dumbbell Press', 1)
        push_slot = self.exercise(push, 'Machine Chest Press', 2)
        self.log('Upper Push', 'Machine Chest Press', 100, 12)

        # Fresh: lead movement on accessories day, 120 lb x 15.
        fresh_slot = self.exercise(accessories, 'Machine Chest Press', 0,
                                   reps='12-15')
        self.log('Accessories', 'Machine Chest Press', 120, 15)

        fatigued = suggest_weight(self.user, push_slot, unit='lb')
        fresh = suggest_weight(self.user, fresh_slot, unit='lb')

        self.assertIsNotNone(fatigued['weight'])
        self.assertIsNotNone(fresh['weight'])
        self.assertLess(
            fatigued['weight'], fresh['weight'],
            'the fatigued slot should suggest less than the fresh one; '
            'fatigued=%s fresh=%s' % (fatigued, fresh))
        # Anchored near their own slot's history, not a pooled average.
        self.assertLess(fatigued['weight'], 110)
        self.assertGreater(fresh['weight'], 105)

    def test_a_higher_rep_target_suggests_less_weight(self):
        day = self.day('Upper Push', 1)
        twelve = self.exercise(day, 'Machine Chest Press', 0, reps='10-12')
        self.log('Upper Push', 'Machine Chest Press', 100, 12)

        at_12 = suggest_weight(self.user, twelve, target_reps='10-12', unit='lb')
        at_15 = suggest_weight(self.user, twelve, target_reps='15-20', unit='lb')
        self.assertLess(at_15['weight'], at_12['weight'])

    # -- fatigue ----------------------------------------------------------

    def test_preceding_same_muscle_work_discounts_the_suggestion(self):
        day = self.day('Upper Push', 1)
        self.exercise(day, 'Barbell Bench Press', 0)
        self.exercise(day, 'Incline Dumbbell Press', 1)
        third = self.exercise(day, 'Machine Chest Press', 2)
        self.assertLess(fatigue_factor(list(day.exercises.all()), 2,
                                       'Machine Chest Press'), 1.0)

    def test_preceding_work_on_other_muscles_does_not(self):
        day = self.day('Full Body', 1)
        self.exercise(day, 'Lying Hamstring Curl', 0)
        press = self.exercise(day, 'Machine Chest Press', 1)
        self.assertEqual(fatigue_factor(list(day.exercises.all()), 1,
                                        'Machine Chest Press'), 1.0)

    def test_the_discount_has_a_floor(self):
        """Below ~20% off it stops being a working weight."""
        day = self.day('Chest Day', 1)
        for i, name in enumerate(['Barbell Bench Press', 'Incline Barbell Press',
                                  'Flat Dumbbell Press', 'Incline Dumbbell Press',
                                  'Machine Chest Press', 'Pec Deck Fly Machine']):
            self.exercise(day, name, i)
        self.assertGreaterEqual(
            fatigue_factor(list(day.exercises.all()), 5, 'Pec Deck Fly Machine'),
            0.80)

    # -- honesty ----------------------------------------------------------

    def test_no_history_returns_no_number(self):
        """
        A guessed number presented as history is the failure mode. Say there
        is nothing rather than inventing a starting weight.
        """
        day = self.day('Upper Push', 1)
        out = suggest_weight(self.user, self.exercise(day, 'Machine Chest Press', 0))
        self.assertIsNone(out['weight'])
        self.assertEqual(out['confidence'], 'none')
        self.assertIn('no logged sets', out['basis'])

    def test_a_bodyweight_exercise_gets_no_weight(self):
        day = self.day('Upper Push', 1)
        pushups = Exercise.objects.create(
            day=day, name='Push-ups', sets=3, reps='15', order=0,
            measurement_type='bodyweight_reps')
        out = suggest_weight(self.user, pushups)
        self.assertIsNone(out['weight'])
        self.assertIn('does not carry a weight', out['basis'])

    def test_the_basis_names_its_source(self):
        day = self.day('Upper Push', 1)
        slot = self.exercise(day, 'Machine Chest Press', 0)
        self.log('Upper Push', 'Machine Chest Press', 100, 12)
        out = suggest_weight(self.user, slot, unit='lb')
        self.assertIn('this slot', out['basis'])
        self.assertEqual(out['confidence'], 'high')

    def test_history_from_another_slot_is_used_but_marked_low_confidence(self):
        """
        Better than nothing, but the user should know it is not from here.
        """
        self.log('Leg Day', 'Machine Chest Press', 100, 12)
        day = self.day('Upper Push', 1)
        out = suggest_weight(self.user, self.exercise(day, 'Machine Chest Press', 0),
                             unit='lb')
        self.assertIsNotNone(out['weight'])
        self.assertEqual(out['confidence'], 'low')
        self.assertIn('other sessions', out['basis'])

    def test_a_missed_target_holds_rather_than_progresses(self):
        day = self.day('Upper Push', 1)
        slot = self.exercise(day, 'Machine Chest Press', 0, reps='12-15')
        self.log('Upper Push', 'Machine Chest Press', 100, 8)
        out = suggest_weight(self.user, slot, unit='lb')
        self.assertLessEqual(out['weight'], 100)
        self.assertIn('missed the target', out['basis'])
