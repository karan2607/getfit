"""
The advice must be coached against the prescription the user is looking at.

Reported from a live session: the guide said

    12 reps at 230.0lb — inside the 10-15 range → HOLD WEIGHT, ADD REPS until 15

while the card on screen said **10-12**. The user's objection was exactly right:
if 12 reps tops the prescribed range then the advice should be "add weight", not
"add reps".

Root cause was not the progression logic. `_exercise_prescription` resolved the
rep target with an unordered `.first()` over every Exercise row the user had ever
owned. Leg Press exists 14 times for this account — 10, 10-12, 12, 8-10 and
10-15 reps across four plans — and the row it happened to return was from a plan
abandoned in June. The card read the active plan's current week; the advice read
whatever the database handed back first.

The fix is to resolve the exact row by id (passed by the drawer), with a
deterministic ordering as the fallback.

Also covered here: the duration increment. "Held 52 sec for 2 sessions →
INCREASE TARGET / Aim for 67 sec" was a flat +15, a 29% jump on a plank.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, gemini, exercise_types as et
from api.models import (
    User, UserProfile, WorkoutPlan, WorkoutDay, Exercise, WorkoutSession,
    SetLog, ExerciseGuide,
)

KG_PER_LB = 0.453592

STABLE_GUIDE = {'steps': ['s'], 'muscles': ['m'], 'tips': ['t'],
                'category': 'other', 'kb_key': None}


class PrescriptionResolutionTests(APITestCase):
    """Which Exercise row the rep target comes from."""

    def setUp(self):
        self.user = User.objects.create_user(email='pres@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        # The shape that produced the bug: an older, inactive plan prescribing a
        # wider range, and the live plan on its current week prescribing 10-12.
        self.old_plan = WorkoutPlan.objects.create(
            user=self.user, title='June', is_active=False, current_week=1)
        old_day = WorkoutDay.objects.create(
            plan=self.old_plan, day_number=2, week_number=1, name='Legs')
        self.old_row = Exercise.objects.create(
            day=old_day, name='Leg Press', sets=3, reps='10-15',
            measurement_type=et.WEIGHT_REPS)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='October', is_active=True, current_week=2)
        # Week 1 is in the past; week 2 is what the user is training.
        wk1 = WorkoutDay.objects.create(
            plan=self.plan, day_number=2, week_number=1, name='Legs')
        Exercise.objects.create(day=wk1, name='Leg Press', sets=3, reps='10',
                                measurement_type=et.WEIGHT_REPS)
        wk2 = WorkoutDay.objects.create(
            plan=self.plan, day_number=4, week_number=2, name='Legs')
        self.current_row = Exercise.objects.create(
            day=wk2, name='Leg Press', sets=3, reps='10-12',
            measurement_type=et.WEIGHT_REPS)

    # -- the resolver itself --------------------------------------------------

    def test_an_explicit_id_wins_over_every_other_row(self):
        _, reps = views._exercise_prescription(
            self.user, 'Leg Press', exercise_id=str(self.old_row.id))
        self.assertEqual(reps, '10-15')
        _, reps = views._exercise_prescription(
            self.user, 'Leg Press', exercise_id=str(self.current_row.id))
        self.assertEqual(reps, '10-12')

    def test_without_an_id_the_active_plans_current_week_wins(self):
        """The regression: this used to be able to return the June row."""
        _, reps = views._exercise_prescription(self.user, 'Leg Press')
        self.assertEqual(reps, '10-12')

    def test_another_users_exercise_id_does_not_resolve(self):
        other = User.objects.create_user(email='pres2@test.local', password='x')
        plan = WorkoutPlan.objects.create(user=other, title='Theirs',
                                          is_active=True, current_week=1)
        day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                        week_number=1, name='Legs')
        theirs = Exercise.objects.create(day=day, name='Leg Press', sets=3,
                                         reps='5', measurement_type=et.WEIGHT_REPS)
        _, reps = views._exercise_prescription(
            self.user, 'Leg Press', exercise_id=str(theirs.id))
        self.assertEqual(reps, '10-12')      # fell back to their own row

    def test_a_junk_id_falls_back_instead_of_raising(self):
        """A malformed UUID reaches a UUIDField lookup and would be a 500."""
        _, reps = views._exercise_prescription(
            self.user, 'Leg Press', exercise_id='not-a-uuid')
        self.assertEqual(reps, '10-12')

    def test_a_stale_id_falls_back(self):
        dead = str(self.old_row.id)
        self.old_plan.delete()
        _, reps = views._exercise_prescription(
            self.user, 'Leg Press', exercise_id=dead)
        self.assertEqual(reps, '10-12')

    def test_an_unknown_name_is_classified_not_guessed(self):
        mtype, reps = views._exercise_prescription(self.user, 'Barbell Squat')
        self.assertEqual(mtype, et.WEIGHT_REPS)
        self.assertIsNone(reps)

    def test_measurement_type_comes_from_the_same_row_as_the_reps(self):
        wk2 = WorkoutDay.objects.get(plan=self.plan, week_number=2)
        plank = Exercise.objects.create(day=wk2, name='Plank', sets=3,
                                        reps='35 sec',
                                        measurement_type=et.DURATION)
        mtype, reps = views._exercise_prescription(
            self.user, 'Plank', exercise_id=str(plank.id))
        self.assertEqual(mtype, et.DURATION)
        self.assertEqual(reps, '35 sec')


class AdviceUsesTheCardsRangeTests(APITestCase):
    """End to end: the reported wrong sentence must not come back."""

    def setUp(self):
        self.user = User.objects.create_user(email='pres3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('exercise-guide')

        old = WorkoutPlan.objects.create(user=self.user, title='June',
                                         is_active=False, current_week=1)
        old_day = WorkoutDay.objects.create(plan=old, day_number=2,
                                            week_number=1, name='Legs')
        Exercise.objects.create(day=old_day, name='Leg Press', sets=3,
                                reps='10-15', measurement_type=et.WEIGHT_REPS)

        plan = WorkoutPlan.objects.create(user=self.user, title='October',
                                          is_active=True, current_week=2)
        day = WorkoutDay.objects.create(plan=plan, day_number=4,
                                        week_number=2, name='Legs')
        self.row = Exercise.objects.create(day=day, name='Leg Press', sets=3,
                                           reps='10-12',
                                           measurement_type=et.WEIGHT_REPS)

        # Three sessions of 3x12 at 230lb — the top of 10-12, inside 10-15.
        for days_ago in (14, 7, 1):
            session = WorkoutSession.objects.create(
                user=self.user, exercise_day=day, day_name='Legs',
                is_completed=True)
            # started_at is auto_now_add, so it has to be backdated after the
            # fact — distinct session dates are what make this history.
            WorkoutSession.objects.filter(pk=session.pk).update(
                started_at=timezone.now() - dt.timedelta(days=days_ago))
            for set_number in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=session, exercise_name='Leg Press',
                    set_number=set_number, weight_kg=230 * KG_PER_LB,
                    reps_completed=12, is_completed=True)

        ExerciseGuide.objects.create(
            user=None, name='Leg Press',
            data=dict(STABLE_GUIDE, advice_version=views.ADVICE_VERSION))

    def get(self, **params):
        res = self.client.get(self.url, dict(params, name='Leg Press'))
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        return res.data

    def test_twelve_reps_on_a_ten_to_twelve_target_adds_weight(self):
        data = self.get(exercise_id=str(self.row.id))
        note = data['coaching_note'] or ''
        self.assertIn('INCREASE WEIGHT', note)
        self.assertNotIn('10-15', note)
        self.assertIn('Go up to', data['recommended_weight'] or '')

    def test_the_reported_sentence_is_gone_even_without_an_id(self):
        note = self.get()['coaching_note'] or ''
        self.assertNotIn('inside the 10-15 range', note)

    def test_pointing_at_the_old_row_still_coaches_that_row(self):
        """Not a bug to fix: 12 reps really is mid-range for 10-15."""
        old = Exercise.objects.get(day__plan__is_active=False, name='Leg Press')
        note = self.get(exercise_id=str(old.id))['coaching_note'] or ''
        self.assertIn('10-15', note)
        self.assertIn('HOLD WEIGHT', note)

    def test_the_response_echoes_the_range_it_coached_against(self):
        data = self.get(exercise_id=str(self.row.id))
        self.assertEqual(data['measurement_type'], et.WEIGHT_REPS)
        self.assertEqual(data['unit'], 'lb')


class DurationIncrementTests(APITestCase):
    """
    Reported: "Held 52 sec for 2 sessions → INCREASE TARGET / Aim for 67 sec —
    based on my sessions that is a massive difference ... especially for an
    exercise like planks."
    """

    def test_a_fifty_two_second_hold_gets_five_seconds_not_fifteen(self):
        self.assertEqual(views._duration_increment(52), 5)

    def test_the_increment_is_proportional(self):
        self.assertEqual(views._duration_increment(100), 10)
        self.assertEqual(views._duration_increment(150), 15)

    def test_short_holds_still_move(self):
        """10% of 20 sec rounds to zero; the floor keeps progression alive."""
        self.assertEqual(views._duration_increment(20), 5)
        self.assertEqual(views._duration_increment(5), 5)

    def test_long_holds_are_capped(self):
        """A 10% demand on a 4-minute hold is 24 sec. Too much in one step."""
        self.assertEqual(views._duration_increment(240), 15)
        self.assertEqual(views._duration_increment(600), 15)

    def test_every_increment_is_a_multiple_of_five(self):
        for seconds in range(5, 400, 7):
            step = views._duration_increment(seconds)
            self.assertEqual(step % 5, 0, msg=f'{seconds}s -> {step}')
            self.assertLessEqual(step, 15)
            self.assertGreaterEqual(step, 5)

    def test_the_increment_never_exceeds_a_quarter_of_the_hold(self):
        """The actual complaint: the jump must stay proportionate."""
        for seconds in range(25, 400, 5):
            self.assertLessEqual(views._duration_increment(seconds),
                                 seconds * 0.25, msg=f'{seconds}s')


class DurationAdviceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='pres4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          is_active=True, current_week=1)
        self.day = WorkoutDay.objects.create(plan=plan, day_number=1,
                                             week_number=1, name='Core')
        self.row = Exercise.objects.create(day=self.day, name='Plank', sets=3,
                                           reps='35 sec',
                                           measurement_type=et.DURATION)
        ExerciseGuide.objects.create(
            user=None, name='Plank',
            data=dict(STABLE_GUIDE, advice_version=views.ADVICE_VERSION))

        # Two sessions holding 52 sec — the exact case that was reported.
        for days_ago in (8, 1):
            session = WorkoutSession.objects.create(
                user=self.user, exercise_day=self.day, day_name='Core',
                is_completed=True)
            WorkoutSession.objects.filter(pk=session.pk).update(
                started_at=timezone.now() - dt.timedelta(days=days_ago))
            for set_number in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=session, exercise_name='Plank',
                    set_number=set_number, duration_seconds=52,
                    is_completed=True)

    def test_a_held_plank_is_asked_for_fifty_seven_not_sixty_seven(self):
        res = self.client.get(reverse('exercise-guide'),
                              {'name': 'Plank',
                               'exercise_id': str(self.row.id)})
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        target = res.data['recommended_target'] or ''
        self.assertIn('57 sec', target)
        self.assertNotIn('67 sec', target)
        self.assertIsNone(res.data['recommended_weight'])
