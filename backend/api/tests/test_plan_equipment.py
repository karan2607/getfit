"""
The gym type, persisted.

`equipment` ('bodyweight' | 'home gym' | 'full gym') was collected at plan
creation, passed into the week-1 generation prompt as a string, and then thrown
away. Nothing stored it, so:

  * every week generated AFTER week 1 was programmed with no idea what
    equipment existed -- a week-5 plan could prescribe cable machines to
    someone who had answered "bodyweight only"
  * the superset validator could not know either, which matters because a
    superset occupies two stations at once

These tests cover the whole path: generate echoes it into the preview, saving
the preview persists it, and the week prompt carries it.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views
from api.models import User, UserProfile, WorkoutPlan, WorkoutDay


class AcceptedEquipmentTests(APITestCase):
    """Unrecognised values are dropped, not rejected."""

    def test_the_three_real_options_are_kept(self):
        for value in ('bodyweight', 'home gym', 'full gym'):
            self.assertEqual(views._accepted_equipment(value), value)

    def test_case_and_padding_are_normalised(self):
        self.assertEqual(views._accepted_equipment('  Full Gym '), 'full gym')

    def test_junk_is_dropped_rather_than_raising(self):
        """
        Same call as _accepted_outline: the value only sharpens the prompt and
        the pairing rules, so a bad one should cost that sharpening and never
        the whole plan.
        """
        for value in ('garage', 'FULL_GYM', 42, {}, ['full gym']):
            self.assertEqual(views._accepted_equipment(value), '')

    def test_missing_means_unknown(self):
        self.assertEqual(views._accepted_equipment(None), '')
        self.assertEqual(views._accepted_equipment(''), '')


class EquipmentPersistsOnSaveTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='equip@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def save_plan(self, payload):
        res = self.client.post(reverse('workout-plans'), payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return WorkoutPlan.objects.get(pk=res.data['id'])

    def body(self, **extra):
        payload = {
            'title': 'P',
            'duration_weeks': 8,
            'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                      'exercises': [{'name': 'Push-ups', 'sets': 3,
                                     'reps': '15'}]}],
        }
        payload.update(extra)
        return payload

    def test_saving_a_preview_persists_the_gym_type(self):
        plan = self.save_plan(self.body(equipment='home gym'))
        self.assertEqual(plan.equipment, 'home gym')

    def test_a_plan_saved_without_it_is_blank_not_broken(self):
        """Older previews and manual plans carry nothing. Blank = unknown."""
        plan = self.save_plan(self.body())
        self.assertEqual(plan.equipment, '')

    def test_an_unrecognised_value_does_not_block_the_save(self):
        plan = self.save_plan(self.body(equipment='my garage'))
        self.assertEqual(plan.equipment, '')

    def test_it_is_exposed_on_the_api(self):
        plan = self.save_plan(self.body(equipment='bodyweight'))
        res = self.client.get(reverse('workout-plan-detail', args=[plan.id]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['equipment'], 'bodyweight')


class GeneratedPreviewCarriesEquipmentTests(APITestCase):
    """
    The link that was missing. The frontend posts the preview object straight
    back on save, so echoing the value into the generate response persists it
    with no client change at all.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='equipgen@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def generate(self, **extra):
        fake_plan = {
            'title': 'Generated',
            'duration_weeks': 8,
            'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                      'exercises': [{'name': 'Push-ups', 'sets': 3,
                                     'reps': '15'}]}],
        }
        payload = {'days_per_week': 4, 'duration_weeks': 8}
        payload.update(extra)
        # Patched at the SOURCE, not on api.views: the generate view
        # re-imports call_gemini_json locally, so a patch on the views module
        # binding is simply not the function that gets called.
        with patch('api.gemini.call_gemini_json', return_value=dict(fake_plan)):
            res = self.client.post(reverse('workout-plan-generate'),
                                   payload, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_the_preview_echoes_the_gym_type_back(self):
        data = self.generate(equipment='home gym')
        self.assertEqual(data['equipment'], 'home gym')

    def test_nothing_is_added_when_none_was_sent(self):
        self.assertNotIn('equipment', self.generate())


class WeekPromptCarriesEquipmentTests(APITestCase):
    """
    THE bug this column exists to fix. Week 1 always knew the gym type; every
    later week was generated blind because the answer was never stored.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='equipwk@test.local', password='x')
        self.profile = UserProfile.objects.create(
            user=self.user, preferred_unit='lb')
        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='P', duration_weeks=8,
            equipment='bodyweight')
        WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')

    def prompt(self):
        system, user = views._week_generate_prompt(
            self.plan, 5, self.profile, preferred_unit='lb')
        return system + '\n' + user

    def test_week_five_is_told_the_gym_type(self):
        self.assertIn('Equipment available: bodyweight', self.prompt())

    def test_an_unknown_gym_type_adds_no_line(self):
        """Blank must not produce "Equipment available: " with nothing after."""
        WorkoutPlan.objects.filter(pk=self.plan.pk).update(equipment='')
        self.plan.refresh_from_db()
        self.assertNotIn('Equipment available:', self.prompt())

    def test_week_one_generation_still_states_it(self):
        """The half that already worked must keep working."""
        _system, user = views._workout_generate_prompt(
            self.profile, days_per_week=4, duration_weeks=8,
            equipment='home gym', preferred_unit='lb')
        self.assertIn('Equipment: home gym', user)
