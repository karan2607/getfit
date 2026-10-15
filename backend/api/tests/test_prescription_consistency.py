"""
The reps prescription must match the exercise's measurement type.

Reported: "Target reps for burpees says Go for 10-15 reps ... but the card says
to do it for 10 mins" and "Although the count column is correctly asking for
reps".

`Metabolic Finisher: Burpees` is bodyweight_reps with reps="10 min": the input
column asks for reps (correct), the advice said reps (correct), and the card said
minutes (wrong). Same class as the earlier Farmer's Carry "40 yards" issue,
which was fixed only in the prompt and never generalised.

Nothing rewrites the prescription — a rep count cannot be inferred from "10 min"
without inventing programming. The prompt forbids producing it, validation logs
it, and the UI tells the user which unit the inputs expect.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, exercise_types as et
from api.models import User, UserProfile, WorkoutPlan, WorkoutDay


class PrescriptionMatchesTypeTests(APITestCase):
    def test_rep_types_reject_a_duration(self):
        """The reported burpees case."""
        ok, expected = et.prescription_matches_type(et.BODYWEIGHT_REPS, '10 min')
        self.assertFalse(ok)
        self.assertEqual(expected, 'reps')
        self.assertFalse(
            et.prescription_matches_type(et.WEIGHT_REPS, '30 sec')[0])

    def test_rep_types_accept_rep_counts(self):
        for reps in ('15', '10-15', '8-12', '10-12 per leg', 'AMRAP',
                     'to failure'):
            self.assertTrue(
                et.prescription_matches_type(et.BODYWEIGHT_REPS, reps)[0],
                msg=reps)

    def test_timed_types_reject_a_distance(self):
        """The Farmer's Carry case."""
        ok, expected = et.prescription_matches_type(et.WEIGHT_DURATION, '40 yards')
        self.assertFalse(ok)
        self.assertEqual(expected, 'time')

    def test_timed_types_reject_a_bare_rep_count(self):
        self.assertFalse(et.prescription_matches_type(et.DURATION, '12')[0])

    def test_timed_types_accept_times(self):
        for reps in ('40 sec', '5-10 min', '35 sec', '20 sec/side', '2 minutes'):
            self.assertTrue(
                et.prescription_matches_type(et.DURATION, reps)[0], msg=reps)

    def test_warmup_accepts_anything(self):
        for reps in ('5-10 min', '12', '40 yards', 'specific drills'):
            self.assertTrue(
                et.prescription_matches_type(et.WARMUP, reps)[0], msg=reps)

    def test_empty_prescription_is_not_a_mismatch(self):
        self.assertTrue(et.prescription_matches_type(et.WEIGHT_REPS, '')[0])
        self.assertTrue(et.prescription_matches_type(et.WEIGHT_REPS, None)[0])

    def test_every_measurement_type_is_handled(self):
        for mtype in et.ALL_TYPES:
            ok, expected = et.prescription_matches_type(mtype, '10')
            self.assertIn(expected, ('reps', 'time', None), msg=mtype)


class PromptForbidsMismatchTests(APITestCase):
    def build(self):
        system, user = views._workout_generate_prompt(
            None, days_per_week=4, duration_weeks=8, preferred_unit='lb')
        return system + '\n' + user

    def test_prompt_states_the_rule_for_every_type(self):
        prompt = self.build()
        self.assertIn('THE REPS FIELD MUST MATCH THE MEASUREMENT TYPE', prompt)
        self.assertIn('[weight_reps], [bodyweight_reps] -> reps', prompt)
        self.assertIn('[duration], [weight_duration]    -> time', prompt)

    def test_prompt_names_both_reported_mistakes(self):
        prompt = self.build()
        self.assertIn('40 yards', prompt)        # the carry case
        self.assertIn('Burpees: 10 min', prompt)  # the burpees case


class ValidationLogsMismatchTests(APITestCase):
    """A mismatch is logged, not rejected, and the prescription is untouched."""

    def setUp(self):
        self.user = User.objects.create_user(email='presc@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')

    def test_mismatched_plan_is_still_accepted(self):
        """Rejecting the plan would be worse than flagging it in the UI."""
        with self.assertLogs('api.views', level='WARNING') as captured:
            res = self.client.put(
                reverse('workout-plan-detail', args=[self.plan.id]), {
                    'title': 'P',
                    'days': [{'day_number': 1, 'name': 'Conditioning',
                              'is_rest_day': False,
                              'exercises': [
                                  {'name': 'Burpees', 'sets': 1, 'reps': '10 min'},
                              ]}],
                }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(any('prescription mismatch' in m for m in captured.output))

    def test_prescription_is_not_rewritten(self):
        self.client.put(reverse('workout-plan-detail', args=[self.plan.id]), {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Conditioning', 'is_rest_day': False,
                      'exercises': [{'name': 'Burpees', 'sets': 1, 'reps': '10 min'}]}],
        }, format='json')
        from api.models import Exercise
        ex = Exercise.objects.get(day__plan=self.plan, name='Burpees')
        self.assertEqual(ex.reps, '10 min',
                         'reps cannot be inferred from a duration; leave it alone')
        self.assertEqual(ex.measurement_type, et.BODYWEIGHT_REPS)

    def test_consistent_plan_logs_nothing(self):
        import logging
        with self.assertLogs('api.views', level='WARNING') as captured:
            logging.getLogger('api.views').warning('sentinel')
            self.client.put(reverse('workout-plan-detail', args=[self.plan.id]), {
                'title': 'P',
                'days': [{'day_number': 1, 'name': 'Upper', 'is_rest_day': False,
                          'exercises': [
                              {'name': 'Barbell Bench Press', 'sets': 3,
                               'reps': '8-12'},
                              {'name': 'Plank', 'sets': 3, 'reps': '45 sec'},
                          ]}],
            }, format='json')
        self.assertFalse(any('prescription mismatch' in m for m in captured.output))
