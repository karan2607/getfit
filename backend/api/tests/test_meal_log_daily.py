"""
Daily protein / carbs / fiber charts on the meal log.

Requested: "Since we get so many meal details daily now, we should also add
charts with daily protein, carbs, and fiber data". Fiber was not stored on a
logged meal at all, so it could never have been charted; `MealLog.fiber_g` is
new and nullable, and `diet/meal-logs/daily/` returns one row per day for the
chart.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api.models import User, UserProfile, MealLog

NOW = dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 11, 22)


class MealLogDailyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='daily@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   timezone='UTC')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('meal-log-daily')

    def log(self, date, **macros):
        return MealLog.objects.create(user=self.user, date=date,
                                      meal_type='lunch', food_name='x', **macros)

    def get(self, **params):
        with mock.patch.object(timezone, 'now', return_value=NOW):
            res = self.client.get(self.url, params)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def test_sums_protein_carbs_and_fiber_per_day(self):
        self.log(TODAY, protein_g=30, carbs_g=40, fiber_g=5)
        self.log(TODAY, protein_g=20, carbs_g=10, fiber_g=7.5)
        self.log(TODAY - dt.timedelta(days=1), protein_g=50, carbs_g=60, fiber_g=3)

        rows = self.get(days=7)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[-1]['date'], TODAY.isoformat())
        self.assertEqual(rows[-1]['meals'], 2)
        self.assertEqual(rows[-1]['protein_g'], 50)
        self.assertEqual(rows[-1]['carbs_g'], 50)
        self.assertEqual(rows[-1]['fiber_g'], 12.5)
        self.assertEqual(rows[-2]['protein_g'], 50)
        self.assertEqual(rows[-2]['fiber_g'], 3)

    def test_empty_days_are_present_so_the_chart_shows_gaps(self):
        rows = self.get(days=14)
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0]['date'], (TODAY - dt.timedelta(days=13)).isoformat())
        self.assertTrue(all(r['meals'] == 0 for r in rows))

    def test_fiber_is_null_when_no_meal_recorded_it(self):
        """Meals logged before fiber existed must not read as 0g of fiber."""
        self.log(TODAY, protein_g=30)
        self.assertIsNone(self.get(days=1)[0]['fiber_g'])

    def test_only_the_callers_meals_are_counted(self):
        other = User.objects.create_user(email='other@test.local', password='x')
        MealLog.objects.create(user=other, date=TODAY, meal_type='lunch',
                               food_name='theirs', protein_g=99)
        self.assertEqual(self.get(days=1)[0]['protein_g'], 0)

    def test_fiber_round_trips_through_the_meal_log_endpoint(self):
        res = self.client.post(reverse('meal-logs'), {
            'date': TODAY.isoformat(), 'meal_type': 'dinner', 'food_name': 'Lentils',
            'protein_g': 18, 'carbs_g': 40, 'fiber_g': 15,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['fiber_g'], 15)
        self.assertEqual(self.get(days=1)[0]['fiber_g'], 15)

    def test_bad_days_is_rejected(self):
        res = self.client.get(self.url, {'days': 'lots'})
        self.assertEqual(res.status_code, 400)

    def test_requires_auth(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
