"""
A chosen unit and an inherited default are different things.

`preferred_unit` defaults to lb and onboarding showed a field labelled kg
without recording a choice, so anyone who typed a number and pressed on got
their kilograms read back as pounds -- 80 kg shown as 176.4 lb, with nothing
on the screen to explain it. Onboarding now always sends the unit it
displayed, which fixes it going forward and does nothing for accounts
already in that state.

Null means "never confirmed". Whether such a profile holds a real choice or
the default nobody saw is a question only the user can settle, so this
records the answer instead of guessing it.
"""
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import User, UserProfile


class UnitConfirmationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='unit@test.local',
                                             password='x')
        self.profile = UserProfile.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_a_fresh_profile_has_not_confirmed_anything(self):
        self.assertIsNone(self.profile.unit_confirmed_at)
        res = self.client.get(reverse('profile'))
        self.assertIsNone(res.data['unit_confirmed_at'])

    def test_sending_a_unit_confirms_it(self):
        res = self.client.patch(reverse('profile'), {'preferred_unit': 'kg'},
                                format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['preferred_unit'], 'kg')
        self.assertIsNotNone(res.data['unit_confirmed_at'])

    def test_the_first_confirmation_is_the_one_kept(self):
        self.client.patch(reverse('profile'), {'preferred_unit': 'kg'},
                          format='json')
        self.profile.refresh_from_db()
        first = self.profile.unit_confirmed_at

        self.client.patch(reverse('profile'), {'preferred_unit': 'lb'},
                          format='json')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.unit_confirmed_at, first)
        self.assertEqual(self.profile.preferred_unit, 'lb')

    def test_updating_something_else_confirms_nothing(self):
        res = self.client.patch(reverse('profile'), {'age': 31},
                                format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.unit_confirmed_at)

    def test_reading_the_profile_never_writes(self):
        self.client.get(reverse('profile'))
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.unit_confirmed_at)
