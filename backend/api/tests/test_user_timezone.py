"""
"Today" must mean the user's day, not the server's.

Reported, with the diagnosis already done:

    "the meals are logged, it's a date mismatch. Your 5 meals ARE stored under
     Oct 19, totaling 2,555 cal. But the GetFit server runs on UTC: when I
     asked it for 'today's' meals with no date, it answered for Oct 20 and
     showed none. You're still on Oct 19 local (11 PM). It should show in
     local time right?"

Yes. Confirmed in production: five meals with `date=2026-10-19` and
`created_at=2026-10-20 05:52+00`. The client sent the correct LOCAL date on
the write; every read of "today" then used `timezone.now().date()`, the UTC
date, and found an empty day. The same skew is present on the 18th and 19th —
it had been happening every night the user logged dinner after ~8pm Eastern.

`UserProfile.timezone` now carries the zone and `user_time.user_today` is the
single way to ask what day it is for someone.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import user_time
from api.models import User, UserProfile, MealLog, HealthDailySummary

# The exact moment from the report: the created_at of the five meals.
# 05:52 UTC is already 2026-10-20 for the server, but 22:52 on 2026-10-19 in
# US Pacific (UTC-7 in October) -- which matches the reported "11 PM", and the
# user's earlier rows (Oct 18 meals written 06:27 UTC) agree. So Pacific is the
# zone that reproduces the bug, not Eastern.
LATE_NIGHT_UTC = dt.datetime(2026, 10, 20, 5, 52, tzinfo=dt.timezone.utc)
USER_ZONE = 'America/Los_Angeles'


class ResolveZoneTests(APITestCase):
    def test_known_zones_resolve(self):
        self.assertEqual(str(user_time.resolve_zone('America/New_York')),
                         'America/New_York')

    def test_unknown_zone_falls_back_to_utc_without_raising(self):
        """A bad stored value must not 500 every health request."""
        self.assertEqual(str(user_time.resolve_zone('Not/AZone')), 'UTC')

    def test_blank_and_none_fall_back_to_utc(self):
        for value in ('', '   ', None):
            self.assertEqual(str(user_time.resolve_zone(value)), 'UTC')


class UserTodayTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tz@test.local', password='x')
        self.profile = UserProfile.objects.create(
            user=self.user, preferred_unit='lb')

    def set_zone(self, name):
        self.profile.timezone = name
        self.profile.save(update_fields=['timezone'])
        self.user.refresh_from_db()

    def test_an_unset_timezone_behaves_exactly_as_before(self):
        """No regression for a profile that never reports one."""
        self.assertEqual(self.profile.timezone, '')
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(user_time.user_today(self.user),
                             dt.date(2026, 10, 20))

    def test_the_reported_case_resolves_to_the_local_day(self):
        self.set_zone(USER_ZONE)
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(user_time.user_today(self.user),
                             dt.date(2026, 10, 19))

    def test_a_zone_ahead_of_utc_can_be_the_next_day(self):
        self.set_zone('Asia/Kolkata')
        with mock.patch.object(
                timezone, 'now',
                return_value=dt.datetime(2026, 10, 19, 20, 0,
                                         tzinfo=dt.timezone.utc)):
            self.assertEqual(user_time.user_today(self.user),
                             dt.date(2026, 10, 20))

    def test_a_junk_stored_zone_degrades_to_utc(self):
        self.set_zone('Mars/Olympus')
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(user_time.user_today(self.user),
                             dt.date(2026, 10, 20))

    def test_a_user_with_no_profile_does_not_raise(self):
        bare = User.objects.create_user(email='tz0@test.local', password='x')
        self.assertIsInstance(user_time.user_today(bare), dt.date)


class MealLogsEndpointTests(APITestCase):
    """The exact endpoint behind the report."""

    def setUp(self):
        self.user = User.objects.create_user(email='tz2@test.local', password='x')
        self.profile = UserProfile.objects.create(
            user=self.user, preferred_unit='lb', timezone=USER_ZONE)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('meal-logs')

        # The five real meals, as stored: local date, next-UTC-day created_at.
        for meal, calories in (('breakfast', 695), ('lunch', 670),
                               ('dinner', 790), ('snack', 240), ('snack', 160)):
            MealLog.objects.create(
                user=self.user, date=dt.date(2026, 10, 19), meal_type=meal,
                food_name='x', calories=calories)

    def test_todays_meals_are_found_at_11pm_local(self):
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 5)
        self.assertEqual(sum(m['calories'] for m in res.data), 2555)

    def test_the_bug_reproduces_with_no_timezone_set(self):
        """
        Proof this test would have caught it: with the zone unset the
        behaviour is the old one, and the day reads empty.
        """
        self.profile.timezone = ''
        self.profile.save(update_fields=['timezone'])
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            res = self.client.get(self.url)
        self.assertEqual(res.data, [])

    def test_an_explicit_date_still_wins(self):
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            res = self.client.get(self.url, {'date': '2026-10-19'})
        self.assertEqual(len(res.data), 5)


class CalorieBalanceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tz3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   timezone=USER_ZONE)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        MealLog.objects.create(
            user=self.user, date=dt.date(2026, 10, 19), meal_type='dinner',
            food_name='x', calories=2555)
        HealthDailySummary.objects.create(
            user=self.user, date=dt.date(2026, 10, 19),
            active_calories=344, resting_calories=1771)

    def test_eaten_and_burned_line_up_on_the_local_day(self):
        """
        The reported symptom: burned was right (2,115) and eaten was 0,
        because only one of them was being asked for the right day.
        """
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            res = self.client.get(reverse('health-calorie-balance'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['calories_in'], 2555)
        self.assertEqual(res.data['burned'], 2115)


class ProfileTimezoneApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='tz4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_the_client_can_report_its_timezone(self):
        res = self.client.patch(reverse('profile'),
                                {'timezone': 'America/New_York'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['timezone'], 'America/New_York')

    def test_an_unknown_timezone_is_rejected(self):
        """
        Validated on write: a junk value would silently shift every date the
        user sees, which is harder to notice than a 400.
        """
        res = self.client.patch(reverse('profile'),
                                {'timezone': 'Not/AZone'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('timezone', res.data)

    def test_blank_is_accepted_and_means_utc(self):
        res = self.client.patch(reverse('profile'), {'timezone': ''},
                                format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['timezone'], '')

    def test_utc_is_accepted(self):
        res = self.client.patch(reverse('profile'), {'timezone': 'UTC'},
                                format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_the_timezone_is_exposed_on_the_profile(self):
        self.assertIn('timezone', self.client.get(reverse('profile')).data)


class McpDateTests(APITestCase):
    """An agent logging a meal at 11pm local means the local day."""

    def setUp(self):
        self.user = User.objects.create_user(email='tz5@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   timezone=USER_ZONE)

    def test_an_omitted_date_is_the_users_today(self):
        from api import mcp
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(mcp._mcp_date({}, user=self.user),
                             dt.date(2026, 10, 19))

    def test_an_explicit_date_is_respected(self):
        from api import mcp
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(mcp._mcp_date({'date': '2026-10-15'},
                                           user=self.user),
                             dt.date(2026, 10, 15))

    def test_tomorrow_in_the_users_zone_is_not_rejected_as_future(self):
        """
        The future check must use the same "today" as the default, or an agent
        in a zone ahead of UTC gets its own current day rejected.
        """
        from api import mcp
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            self.assertEqual(mcp._mcp_date({'date': '2026-10-19'},
                                           user=self.user),
                             dt.date(2026, 10, 19))

    def test_a_genuinely_future_date_is_still_rejected(self):
        from api import mcp
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            with self.assertRaises(mcp.McpError):
                mcp._mcp_date({'date': '2027-01-01'}, user=self.user)


class HealthSeriesTimezoneTests(APITestCase):
    def test_the_newest_bucket_is_the_users_today(self):
        from api.health_series import build_series

        user = User.objects.create_user(email='tz6@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb',
                                   timezone=USER_ZONE)
        HealthDailySummary.objects.create(
            user=user, date=dt.date(2026, 10, 19), steps=9000)
        with mock.patch.object(timezone, 'now', return_value=LATE_NIGHT_UTC):
            series = build_series(user, 'daily')
        self.assertEqual(series['to'], '2026-10-19')
        self.assertEqual(series['buckets'][-1]['steps'], 9000)
