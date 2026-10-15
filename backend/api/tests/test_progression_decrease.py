"""
Regression tests for the progression recommendation.

Reported: "It's saying You consistently hit 8-9 reps at 90.0lb last session.
Let's keep working on increasing that rep count... But the needed reps were and
are 15-20 so ideally it should ask me to lower weights right? In fact it can go
back historically and see I am hitting the reps at 45/50 lbs."

Exactly right. Two defects:

1. The decrease branch required BOTH of the last two sessions to have failed. A
   jump from 50lb (20 reps) to 90lb (8 reps) therefore fell through to
   "maintain" -- advising the user to stay at a weight they had just failed at
   by more than half.
2. History was never mined for the heaviest weight at which the target HAD been
   achieved, so the advice could not point back to the 50lb that worked.

The fixture below is the real production history for Seated Calf Raise.
"""

import datetime as dt

from rest_framework.test import APITestCase

from api import views, exercise_types as et

KG_PER_LB = 0.453592


def session(day, weight_lb, reps_list, month=10):
    """One session's worth of logs at a given weight."""
    return [
        {
            'workout_session__started_at': dt.datetime(2026, month, day, 10, 0),
            'set_number': i + 1,
            'weight_kg': weight_lb * KG_PER_LB,
            'reps_completed': reps,
            'duration_seconds': None,
        }
        for i, reps in enumerate(reps_list)
    ]


class OverJumpDecreaseTests(APITestCase):
    """The reported case: one session badly short, after a successful one."""

    def analyze(self, logs, target='15-20'):
        return views._analyze_progression(
            logs,
            target_reps=views._target_rep_range(target),
            measurement_type=et.WEIGHT_REPS,
            unit='lb',
        )

    def real_history(self):
        """Production history for Seated Calf Raise, newest first."""
        return (
            session(7, 90, [9, 8, 8])          # over-jumped, badly short
            + session(1, 45, [20, 20, 20])     # target hit
            + session(19, 50, [20, 20, 20], month=8)
            + session(12, 50, [20, 20, 20], month=8)
        )

    def test_single_bad_session_recommends_decrease_not_maintain(self):
        res = self.analyze(self.real_history())
        self.assertEqual(res['direction'], 'decrease',
                         'one session at half the target must not be "maintain"')
        self.assertIn('DECREASE WEIGHT', res['summary'])

    def test_advice_names_the_weight_the_target_was_hit_at(self):
        res = self.analyze(self.real_history())
        self.assertEqual(res['best_at_target'], 50.0)
        self.assertIn('50.0lb', res['summary'])
        self.assertIn('go back to', res['summary'])

    def test_summary_states_the_actual_target(self):
        res = self.analyze(self.real_history())
        self.assertIn('target 15-20', res['summary'])

    def test_summary_is_in_the_users_unit(self):
        res = self.analyze(self.real_history())
        self.assertIn('lb', res['summary'])
        self.assertNotIn('kg', res['summary'])

    def test_kg_user_gets_kg(self):
        res = views._analyze_progression(
            self.real_history(),
            target_reps=20, measurement_type=et.WEIGHT_REPS, unit='kg')
        self.assertEqual(res['direction'], 'decrease')
        self.assertIn('kg', res['summary'])

    def test_two_sessions_below_the_range_decrease(self):
        logs = (session(7, 80, [13, 12, 12])    # below the 15-20 range
                + session(1, 80, [13, 13, 12]))
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')

    def test_reps_inside_the_range_hold_rather_than_back_off(self):
        """
        17-18 reps against a prescribed 15-20 is INSIDE the range. The old
        max()-only logic called this a failure; holding the weight and chasing
        reps is the correct verdict.
        """
        logs = (session(7, 80, [18, 17, 17])
                + session(1, 80, [18, 18, 17]))
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'maintain')
        self.assertIn('ADD REPS', res['summary'])

    def test_hitting_target_twice_at_same_weight_increases(self):
        logs = session(7, 50, [20, 20, 20]) + session(1, 50, [20, 20, 20])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('INCREASE WEIGHT', res['summary'])

    def test_no_backward_recommendation_when_nothing_better_exists(self):
        """If the only history is the failed weight, don't invent a target."""
        logs = session(7, 90, [9, 8, 8]) + session(1, 90, [9, 9, 8])
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')
        self.assertIsNone(res['best_at_target'])
        self.assertNotIn('go back to', res['summary'])

    def test_does_not_suggest_going_back_to_a_heavier_weight(self):
        """best_at_target is only mentioned when it is LOWER than the failure."""
        logs = (session(7, 40, [9, 8, 8])        # failed light
                + session(1, 60, [20, 20, 20]))  # succeeded heavier
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')
        self.assertNotIn('go back to', res['summary'])

    def test_history_window_reaches_back_far_enough(self):
        """
        The proven weight can be many sessions back. With a 4-session window the
        50lb evidence was invisible; it is 8 now.
        """
        logs = (session(7, 90, [9, 8, 8])
                + session(6, 90, [9, 8, 8])
                + session(5, 85, [10, 10, 9])
                + session(4, 80, [12, 11, 11])
                + session(3, 70, [14, 13, 13])
                + session(2, 50, [20, 20, 20]))   # 6 sessions back
        res = self.analyze(logs)
        self.assertEqual(res['direction'], 'decrease')
        self.assertEqual(res['best_at_target'], 50.0)


class NonWeightTypesUnaffectedTests(APITestCase):
    """The new decrease rule must not leak into time or rep based work."""

    def test_duration_progression_unchanged(self):
        logs = [
            {'workout_session__started_at': dt.datetime(2026, 10, d, 10, 0),
             'set_number': 1, 'weight_kg': None, 'reps_completed': None,
             'duration_seconds': secs}
            for d, secs in ((7, 60), (6, 60), (5, 60))
        ]
        res = views._analyze_progression(
            logs, measurement_type=et.DURATION, unit='lb')
        self.assertEqual(res['direction'], 'increase')
        self.assertIn('sec', res['summary'])

    def test_warmup_still_silent(self):
        logs = session(7, 50, [20, 20, 20])
        res = views._analyze_progression(
            logs, measurement_type=et.WARMUP, unit='lb')
        self.assertIsNone(res['direction'])


class PromptCarriesTargetTests(APITestCase):
    """
    The model said "keep working on increasing that rep count" because the
    prompt never told it the target was 15-20.

    Asserted against the real generated prompt by driving the endpoint with a
    stubbed Gemini — inspect.getsource() on the view returns DRF's @api_view
    wrapper, not the function body.
    """

    def setUp(self):
        from django.utils import timezone
        from rest_framework.authtoken.models import Token
        from api.models import (
            User, UserProfile, WorkoutPlan, WorkoutDay, Exercise,
            WorkoutSession, SetLog,
        )

        self.user = User.objects.create_user(email='prog@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', is_active=True, current_week=1)
        day = WorkoutDay.objects.create(plan=self.plan, day_number=1,
                                        week_number=1, name='Legs')
        calf = Exercise.objects.create(
            day=day, name='Seated Calf Raise', sets=3, reps='15-20',
            measurement_type=et.WEIGHT_REPS)

        # Two sessions: 50lb hitting 20, then 90lb managing 8.
        for d, weight_lb, reps in ((1, 50, 20), (7, 90, 8)):
            s = WorkoutSession.objects.create(user=self.user, exercise_day=day,
                                              day_name='Legs')
            WorkoutSession.objects.filter(pk=s.pk).update(
                started_at=timezone.make_aware(dt.datetime(2026, 10, d, 10, 0)))
            for n in (1, 2, 3):
                SetLog.objects.create(
                    workout_session=s, exercise=calf,
                    exercise_name='Seated Calf Raise', set_number=n,
                    weight_kg=weight_lb * KG_PER_LB, reps_completed=reps,
                    is_completed=True)

    def captured_prompt(self):
        from unittest import mock
        from django.urls import reverse
        from api import gemini

        captured = {}

        def _call(*, system_prompt, user_prompt, **kwargs):
            captured['user'] = user_prompt
            count = sum(1 for line in user_prompt.splitlines()
                        if line[:3].strip().rstrip('.').isdigit() and '[' in line)
            return [{'steps': ['a'], 'muscles': ['Calves'], 'tips': ['t'],
                     'category': 'other', 'kb_key': None}
                    for _ in range(count)]

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            res = self.client.post(
                reverse('workout-plan-prepare-week', args=[self.plan.id]),
                {}, format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))
        return captured['user']

    def test_prompt_states_the_prescribed_target(self):
        self.assertIn('TARGET: 15-20', self.captured_prompt())

    def test_prompt_tells_the_model_the_hint_overrides_it(self):
        self.assertIn('OVERRIDES your own judgement', self.captured_prompt())

    def test_prompt_carries_the_decrease_signal_and_the_proven_weight(self):
        prompt = self.captured_prompt()
        self.assertIn('DECREASE WEIGHT', prompt)
        # The summary now names the full prescribed range rather than only its
        # maximum, since reaching the bottom of a range is not a failure.
        self.assertIn('Only 8 reps at 90.0lb last session (target 15-20)', prompt)
        self.assertIn('Previously hit 15+ reps at 50.0lb', prompt)
        self.assertIn('go back to about 50.0lb', prompt)
        self.assertNotIn('kg', prompt)


class LoadedCarryPrescriptionTests(APITestCase):
    """
    Reported: "farmers carry is asking to calculate in time but the required
    measurement is in yards."

    A weight_duration exercise is logged as weight + seconds, so prescribing it
    in yards leaves the user guessing which number goes in which box. Full
    distance support is a separate change; the prompt must at least stop
    producing the contradiction.
    """

    def test_prompt_forbids_distance_prescriptions_for_carries(self):
        """
        The carry-specific rule was generalised into one rule covering every
        measurement type, after the same class of bug appeared for burpees.
        """
        system, _user = views._workout_generate_prompt(
            None, days_per_week=4, duration_weeks=8, preferred_unit='lb')
        self.assertIn('THE REPS FIELD MUST MATCH THE MEASUREMENT TYPE', system)
        self.assertIn('Never prescribe a loaded carry in yards', system)

    def test_weight_duration_contract_is_weight_plus_seconds(self):
        self.assertEqual(
            tuple(et.FIELD_CONTRACT[et.WEIGHT_DURATION]),
            ('weight_kg', 'duration_seconds'))
