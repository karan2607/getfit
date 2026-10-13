"""
Rep schemes must read like programming, not machine output.

Reported: "while it is smarter in suggesting sets/exercises it is not being
extremely smart. It suggests doing 11, 13, 16 sets. That's not the gym way."

Production had 70 exercises prescribing bare 7, 9, 11, 13, 14, 16, 17 or 18.
Fixed by constraining the prompt and snapping on write; snapping widens to the
bracket containing the original, so the stimulus is unchanged.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, exercise_types as et
from api.models import User, UserProfile, WorkoutPlan, WorkoutDay, Exercise


class NormalizeRepsTests(APITestCase):
    def test_arbitrary_numbers_snap_to_a_bracket_that_contains_them(self):
        cases = {
            '7': '6-8', '9': '8-10', '11': '10-12', '13': '12-15',
            '14': '12-15', '16': '15-20', '17': '15-20', '18': '15-20',
            '19': '15-20', '21': '20-25',
        }
        for raw, expected in cases.items():
            self.assertEqual(et.normalize_reps(raw), expected, msg=raw)
            # The original number must fall inside the bracket it snapped to.
            low, high = (int(p) for p in expected.split('-'))
            self.assertTrue(low <= int(raw) <= high,
                            msg='%s snapped outside its own range' % raw)

    def test_conventional_singles_are_left_alone(self):
        for raw in ('1', '3', '5', '6', '8', '10', '12', '15', '20', '25', '30'):
            self.assertEqual(et.normalize_reps(raw), raw, msg=raw)

    def test_existing_ranges_are_left_alone(self):
        for raw in ('6-8', '8-10', '8-12', '10-12', '12-15', '15-20', '3-5',
                    '20-25'):
            self.assertEqual(et.normalize_reps(raw), raw, msg=raw)

    def test_durations_are_never_treated_as_reps(self):
        """A plank's "30 sec" must not become "25-30 sec"."""
        for raw in ('30 sec', '45 seconds', '5-10 min', '10 min', '2 minutes',
                    '20 sec/side', '6 hours', '90s'):
            self.assertEqual(et.normalize_reps(raw), raw, msg=raw)

    def test_distances_are_left_alone(self):
        for raw in ('30 yards', '40 yards', '100 meters', '1 mile', '13 km'):
            self.assertEqual(et.normalize_reps(raw), raw, msg=raw)

    def test_non_numeric_prescriptions_are_left_alone(self):
        for raw in ('AMRAP', 'to failure', 'as many as possible', ''):
            self.assertEqual(et.normalize_reps(raw), raw, msg=raw)

    def test_unilateral_qualifiers_are_preserved(self):
        self.assertEqual(et.normalize_reps('11 per leg'), '10-12 per leg')
        self.assertEqual(et.normalize_reps('13 reps'), '12-15 reps')
        self.assertEqual(et.normalize_reps('10-12 per leg'), '10-12 per leg')

    def test_set_notation_is_left_alone(self):
        self.assertEqual(et.normalize_reps('3x12'), '3x12')

    def test_is_idempotent(self):
        for raw in ('13', '11 per leg', '30 sec', '8-12'):
            once = et.normalize_reps(raw)
            self.assertEqual(et.normalize_reps(once), once, msg=raw)

    def test_handles_none(self):
        self.assertIsNone(et.normalize_reps(None))


class RepsNormalizedOnWriteTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='reps@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.plan = WorkoutPlan.objects.create(user=self.user, title='P')
        self.day = WorkoutDay.objects.create(plan=self.plan, day_number=1,
                                             week_number=1, name='Upper Body')

    def test_create_exercise_normalizes(self):
        ex = views._create_exercise(self.day, {
            'name': 'Barbell Bench Press', 'sets': 3, 'reps': '13'}, 0)
        self.assertEqual(ex.reps, '12-15')

    def test_plan_replace_normalizes(self):
        res = self.client.put(reverse('workout-plan-detail', args=[self.plan.id]), {
            'title': 'P',
            'days': [{'day_number': 1, 'name': 'Upper Body', 'is_rest_day': False,
                      'exercises': [
                          {'name': 'Barbell Bench Press', 'sets': 3, 'reps': '11'},
                          {'name': 'Plank', 'sets': 3, 'reps': '45 sec'},
                          {'name': 'Barbell Squat', 'sets': 3, 'reps': '8-12'},
                      ]}],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        reps = dict(Exercise.objects.filter(day__plan=self.plan)
                    .values_list('name', 'reps'))
        self.assertEqual(reps['Barbell Bench Press'], '10-12')
        self.assertEqual(reps['Plank'], '45 sec', 'a duration must survive')
        self.assertEqual(reps['Barbell Squat'], '8-12')

    def test_normalizing_does_not_change_measurement_type(self):
        """Snapping reps must not disturb duration classification."""
        plank = views._create_exercise(self.day, {
            'name': 'Plank', 'sets': 3, 'reps': '45 sec'}, 0)
        self.assertEqual(plank.measurement_type, et.DURATION)
        self.assertEqual(plank.reps, '45 sec')

    def test_no_exercise_in_db_has_an_unconventional_bare_rep_count(self):
        for raw in ('7', '9', '11', '13', '14', '16', '17', '18'):
            views._create_exercise(self.day, {
                'name': 'Lift ' + raw, 'sets': 3, 'reps': raw}, 0)
        bare = [
            r for r in Exercise.objects.values_list('reps', flat=True)
            if r.isdigit() and int(r) not in et._CONVENTIONAL_SINGLES
        ]
        self.assertEqual(bare, [])


class PromptConstrainsRepsTests(APITestCase):
    def build(self, unit='lb'):
        system, user = views._workout_generate_prompt(
            None, days_per_week=4, duration_weeks=8,
            fitness_goal='build_muscle', experience_level='intermediate',
            equipment='full gym', preferred_unit=unit)
        return system + '\n' + user

    def test_prompt_lists_the_allowed_schemes(self):
        prompt = self.build()
        self.assertIn('CONVENTIONAL SCHEMES ONLY', prompt)
        for scheme in ('8-12', '10-12', '12-15', '15-20', '6-8', 'AMRAP'):
            self.assertIn(scheme, prompt, msg=scheme)

    def test_prompt_names_the_bad_values_explicitly(self):
        prompt = self.build()
        for bad in ('"11"', '"13"', '"14"', '"17"'):
            self.assertIn(bad, prompt, msg=bad)


class DataUpgradeRepsTests(APITestCase):
    """Existing plans get repaired in place, per the stated requirement."""

    def test_upgrade_normalizes_existing_reps_without_losing_anything(self):
        from django.core.management import call_command

        user = User.objects.create_user(email='upreps@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        plan = WorkoutPlan.objects.create(user=user, title='Legacy')
        day = WorkoutDay.objects.create(plan=plan, day_number=1, week_number=1,
                                        name='Upper Body')
        odd = Exercise.objects.create(day=day, name='Bench', sets=3, reps='13',
                                      measurement_type=et.WEIGHT_REPS)
        plank = Exercise.objects.create(day=day, name='Plank', sets=3,
                                        reps='45 sec',
                                        measurement_type=et.DURATION)
        fine = Exercise.objects.create(day=day, name='Squat', sets=3, reps='8-12',
                                       measurement_type=et.WEIGHT_REPS)

        call_command('upgrade_data', verbosity=0)

        odd.refresh_from_db()
        plank.refresh_from_db()
        fine.refresh_from_db()
        self.assertEqual(odd.reps, '12-15')
        self.assertEqual(plank.reps, '45 sec')
        self.assertEqual(fine.reps, '8-12')
        self.assertEqual(Exercise.objects.filter(day=day).count(), 3)
