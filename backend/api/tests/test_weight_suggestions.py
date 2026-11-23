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
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import (
    Composite, Exercise, SetLog, User, UserProfile, WorkoutDay, WorkoutPlan,
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


class AcceptanceScenarioTests(TestCase):
    """
    The scenario as specified, built the way real sessions are: the plan rows
    exist, the session pre-populates a SetLog per planned set, and history
    carries each set's position through its Exercise FK.

      push day:        Bench + Push-ups (composite), Incline DB, Machine Press
      accessories day: Zone 2 cardio, Machine Press (lead strength movement)
    """

    def setUp(self):
        self.user = User.objects.create_user(email='a@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True)
        self.push = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')
        composite = Composite.objects.create(day=self.push, order=0)
        self.bench = self.ex(self.push, 'Barbell Bench Press', 0, '6-8',
                             composite=composite)
        self.pushups = self.ex(self.push, 'Push-ups', 1, '10-15',
                               mtype='bodyweight_reps', composite=composite)
        self.incline = self.ex(self.push, 'Incline Dumbbell Press', 2, '8-12')
        self.push_press = self.ex(self.push, 'Machine Chest Press', 3, '10-12')

        self.acc = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1,
            name='Cardio/Abs/Accessories')
        self.ex(self.acc, 'Zone 2 Cardio', 0, '20 min', mtype='duration')
        self.acc_press = self.ex(self.acc, 'Machine Chest Press', 1, '12-15')

        # History: 100 x 12 on push day (after bench + incline), 120 x 15 on
        # accessories day (first strength movement).
        self.past(self.push, {self.bench: (185, 8), self.pushups: (None, 15),
                              self.incline: (60, 10),
                              self.push_press: (100, 12)}, days_ago=8)
        self.past(self.acc, {self.acc_press: (120, 15)}, days_ago=5)

    def ex(self, day, name, order, reps, mtype='weight_reps', composite=None):
        return Exercise.objects.create(
            day=day, name=name, sets=3, reps=reps, order=order,
            measurement_type=mtype, composite=composite)

    def past(self, day, results, days_ago, sets=3):
        session = WorkoutSession.objects.create(
            user=self.user, exercise_day=day, day_name=day.name,
            is_completed=True)
        WorkoutSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago))
        for ex, (lb, reps) in results.items():
            for n in range(1, sets + 1):
                SetLog.objects.create(
                    workout_session=session, exercise=ex,
                    exercise_name=ex.name, target_reps=ex.reps, set_number=n,
                    weight_kg=lb * LB if lb is not None else None,
                    reps_completed=reps, is_completed=True)
        return session

    def open_session(self, day):
        """What POST /workouts/sessions/ does: one blank SetLog per set."""
        res = self.client.post(reverse('workout-sessions'),
                               {'exercise_day_id': str(day.id), 'force': True},
                               format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        return res.json()

    def login(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # -- 1. slot-differentiated, both directions -------------------------

    def test_push_day_suggests_about_100_and_accessories_about_120(self):
        self.login()
        push = self.open_session(self.push)['weight_suggestions']
        self.assertAlmostEqual(push[str(self.push_press.id)]['weight'], 100, delta=5)
        acc = self.open_session(self.acc)['weight_suggestions']
        self.assertAlmostEqual(acc[str(self.acc_press.id)]['weight'], 120, delta=5)

    # -- 2. only the target reps change ----------------------------------

    def test_a_15_rep_push_target_comes_in_below_the_12_rep_one(self):
        at_12 = suggest_weight(self.user, self.push_press, unit='lb')
        at_15 = suggest_weight(self.user, self.push_press, target_reps='15',
                               unit='lb')
        self.assertLess(at_15['weight'], at_12['weight'])

    # -- 3. completion trend ---------------------------------------------

    def test_all_sets_at_the_top_on_a_fresh_slot_holds_or_bumps(self):
        out = suggest_weight(self.user, self.acc_press, unit='lb')
        self.assertGreaterEqual(out['weight'], 120)
        self.assertLessEqual(out['weight'], 125)

    def test_missed_reps_last_time_pulls_back(self):
        missed = self.past(self.acc, {self.acc_press: (120, 15)}, days_ago=1)
        SetLog.objects.filter(workout_session=missed, set_number=3).update(
            reps_completed=9)
        out = suggest_weight(self.user, self.acc_press, unit='lb')
        self.assertLess(out['weight'], 120)
        self.assertIn('missed', out['basis'])

    # -- 4. live, from today's actuals -----------------------------------

    def _live(self, session_id, reps_on_incline):
        actuals = [{'exercise_id': str(self.incline.id), 'set_number': n,
                    'reps_completed': reps_on_incline, 'is_completed': True}
                   for n in (1, 2, 3)]
        res = self.client.post(
            reverse('workout-session-suggestions', args=[session_id]),
            {'actuals': actuals}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['weight_suggestions'][str(self.push_press.id)]['weight']

    def test_beating_early_targets_nudges_a_later_suggestion_up(self):
        self.login()
        session = self.open_session(self.push)
        planned = session['weight_suggestions'][str(self.push_press.id)]['weight']
        self.assertGreater(self._live(session['id'], 15), planned)   # 8-12 asked
        self.assertLess(self._live(session['id'], 5), planned)

    def test_live_suggestions_read_saved_sets_too(self):
        self.login()
        session = self.open_session(self.push)
        planned = session['weight_suggestions'][str(self.push_press.id)]['weight']
        for n in (1, 2, 3):
            self.client.post(
                reverse('workout-session-log-set', args=[session['id']]),
                {'exercise_id': str(self.incline.id), 'set_number': n,
                 'weight_kg': 60 * LB, 'reps_completed': 15,
                 'is_completed': True}, format='json')
        res = self.client.get(
            reverse('workout-session-detail', args=[session['id']]))
        live = res.json()['weight_suggestions'][str(self.push_press.id)]['weight']
        self.assertGreater(live, planned)

    # -- 5. a suggestion is never logged ---------------------------------

    def test_opening_and_recomputing_writes_no_weight(self):
        self.login()
        session = self.open_session(self.push)
        self._live(session['id'], 15)
        logs = SetLog.objects.filter(workout_session_id=session['id'])
        self.assertTrue(logs.exists())
        self.assertFalse(logs.filter(weight_kg__isnull=False).exists())
        self.assertFalse(logs.filter(is_completed=True).exists())

    # -- 6. no history ----------------------------------------------------

    def test_no_history_exercise_gets_a_blank_not_a_number(self):
        fly = self.ex(self.push, 'Cable Fly', 4, '12-15')
        self.login()
        out = self.open_session(self.push)['weight_suggestions'][str(fly.id)]
        self.assertIsNone(out['weight'])
        self.assertEqual(out['confidence'], 'none')

    # -- 7. composite components -----------------------------------------

    def test_composite_components_each_get_their_own(self):
        self.login()
        s = self.open_session(self.push)['weight_suggestions']
        self.assertAlmostEqual(s[str(self.bench.id)]['weight'], 185, delta=10)
        self.assertNotIn(str(self.pushups.id), s)    # bodyweight: none

    # -- never absurd ----------------------------------------------------

    def test_stacked_adjustments_stay_near_what_was_lifted(self):
        out = suggest_weight(self.user, self.push_press, target_reps='30',
                             unit='lb')
        self.assertGreaterEqual(out['weight'], 70)
