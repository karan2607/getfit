"""
Time-bucketed health series.

Backs the daily / weekly / 6-month chart toggle and the `get_health_series` MCP
tool. Both call build_series, so these tests cover what a chart and an agent
will each report.

Two decisions worth pinning down:

* Metrics are PER-DAY AVERAGES within a bucket, not bucket totals. A weekly step
  total is ~7x a daily one, so totals would make the y-axis jump an order of
  magnitude on every toggle and hide the trend.
* Empty buckets are still returned, with null values. A sync gap must read as a
  gap rather than silently compressing the x-axis.
"""

import datetime as dt

from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token
from django.urls import reverse

from api.health_series import build_series, RANGES, METRICS
from api.models import User, UserProfile, HealthDailySummary

TODAY = dt.date(2026, 10, 15)      # a Thursday


class SeriesBuilderTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='hs1@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')

    def add(self, day, **metrics):
        HealthDailySummary.objects.create(user=self.user, date=day, **metrics)

    def days_ago(self, n):
        return TODAY - dt.timedelta(days=n)

    # -- shape ---------------------------------------------------------------

    def test_daily_range_returns_one_bucket_per_day(self):
        series = build_series(self.user, 'daily', today=TODAY)
        self.assertEqual(series['bucket'], 'day')
        self.assertEqual(len(series['buckets']), 14)
        self.assertEqual(series['buckets'][-1]['start'], TODAY.isoformat())

    def test_weekly_range_buckets_by_week(self):
        series = build_series(self.user, 'weekly', today=TODAY)
        self.assertEqual(series['bucket'], 'week')
        self.assertGreaterEqual(len(series['buckets']), 12)
        # Weeks start on Monday.
        for b in series['buckets']:
            self.assertEqual(dt.date.fromisoformat(b['start']).weekday(), 0)

    def test_monthly_range_buckets_by_month(self):
        series = build_series(self.user, 'monthly', today=TODAY)
        self.assertEqual(series['bucket'], 'month')
        for b in series['buckets']:
            self.assertEqual(dt.date.fromisoformat(b['start']).day, 1)
        self.assertGreaterEqual(len(series['buckets']), 6)

    def test_unknown_range_falls_back_to_the_default(self):
        series = build_series(self.user, 'fortnightly', today=TODAY)
        self.assertEqual(series['range'], 'daily')

    def test_every_metric_appears_on_every_bucket(self):
        series = build_series(self.user, 'daily', today=TODAY)
        for bucket in series['buckets']:
            for metric in METRICS:
                self.assertIn(metric, bucket)

    # -- averaging -----------------------------------------------------------

    def test_weekly_value_is_the_daily_average_not_the_total(self):
        """7 days of 10,000 steps is 10,000 — not 70,000."""
        monday = TODAY - dt.timedelta(days=TODAY.weekday())
        for i in range(4):
            self.add(monday + dt.timedelta(days=i), steps=10000)
        series = build_series(self.user, 'weekly', today=TODAY)
        current = series['buckets'][-1]
        self.assertEqual(current['steps'], 10000)
        self.assertEqual(current['days_with_data'], 4)

    def test_average_ignores_missing_days(self):
        monday = TODAY - dt.timedelta(days=TODAY.weekday())
        self.add(monday, steps=10000)
        self.add(monday + dt.timedelta(days=1), steps=20000)
        series = build_series(self.user, 'weekly', today=TODAY)
        self.assertEqual(series['buckets'][-1]['steps'], 15000)

    def test_a_metric_missing_on_some_days_averages_only_its_own_days(self):
        monday = TODAY - dt.timedelta(days=TODAY.weekday())
        self.add(monday, steps=8000, sleep_hours=7)
        self.add(monday + dt.timedelta(days=1), steps=12000)   # no sleep
        bucket = build_series(self.user, 'weekly', today=TODAY)['buckets'][-1]
        self.assertEqual(bucket['steps'], 10000)
        self.assertEqual(bucket['sleep_hours'], 7)

    # -- gaps ----------------------------------------------------------------

    def test_days_with_no_data_are_present_as_nulls(self):
        self.add(TODAY, steps=9000)
        series = build_series(self.user, 'daily', today=TODAY)
        self.assertEqual(len(series['buckets']), 14)
        self.assertEqual(series['buckets'][-1]['steps'], 9000)
        self.assertIsNone(series['buckets'][0]['steps'])
        self.assertEqual(series['buckets'][0]['days_with_data'], 0)

    def test_has_data_is_false_when_the_window_is_empty(self):
        self.assertFalse(build_series(self.user, 'daily', today=TODAY)['has_data'])

    def test_has_data_is_true_with_one_row(self):
        self.add(TODAY, steps=1)
        self.assertTrue(build_series(self.user, 'daily', today=TODAY)['has_data'])

    def test_data_outside_the_window_is_excluded(self):
        """A five-month-old row must not leak into the 14-day view."""
        self.add(self.days_ago(150), steps=6340)
        daily = build_series(self.user, 'daily', today=TODAY)
        self.assertFalse(daily['has_data'])
        monthly = build_series(self.user, 'monthly', today=TODAY)
        self.assertTrue(monthly['has_data'])

    # -- summary -------------------------------------------------------------

    def test_summary_reports_latest_previous_and_change(self):
        self.add(self.days_ago(1), steps=8000)
        self.add(TODAY, steps=10000)
        stat = build_series(self.user, 'daily', today=TODAY)['summary']['steps']
        self.assertEqual(stat['latest'], 10000)
        self.assertEqual(stat['previous'], 8000)
        self.assertEqual(stat['change'], 2000)
        self.assertEqual(stat['change_pct'], 25.0)

    def test_summary_skips_empty_buckets_when_finding_the_previous_value(self):
        """A rest day between two synced days must not read as a 100% drop."""
        self.add(self.days_ago(5), steps=8000)
        self.add(TODAY, steps=9000)
        stat = build_series(self.user, 'daily', today=TODAY)['summary']['steps']
        self.assertEqual(stat['latest'], 9000)
        self.assertEqual(stat['previous'], 8000)

    def test_summary_is_all_none_without_data(self):
        stat = build_series(self.user, 'daily', today=TODAY)['summary']['steps']
        self.assertIsNone(stat['latest'])
        self.assertIsNone(stat['change'])

    def test_single_point_has_no_change(self):
        self.add(TODAY, steps=9000)
        stat = build_series(self.user, 'daily', today=TODAY)['summary']['steps']
        self.assertEqual(stat['latest'], 9000)
        self.assertIsNone(stat['previous'])
        self.assertIsNone(stat['change'])

    def test_zero_previous_does_not_divide_by_zero(self):
        self.add(self.days_ago(1), resting_heart_rate=0)
        self.add(TODAY, resting_heart_rate=60)
        stat = build_series(self.user, 'daily',
                            today=TODAY)['summary']['resting_heart_rate']
        self.assertIsNone(stat['change_pct'])

    # -- isolation -----------------------------------------------------------

    def test_another_users_data_is_not_included(self):
        other = User.objects.create_user(email='hs2@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='kg')
        HealthDailySummary.objects.create(user=other, date=TODAY, steps=99999)
        self.assertFalse(build_series(self.user, 'daily', today=TODAY)['has_data'])


class SeriesEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='hs3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('health-series')

    def test_defaults_to_daily(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['range'], 'daily')

    def test_each_range_is_accepted(self):
        for key in RANGES:
            res = self.client.get(self.url, {'range': key})
            self.assertEqual(res.status_code, 200, msg=key)
            self.assertEqual(res.data['range'], key)

    def test_bad_range_is_400(self):
        res = self.client.get(self.url, {'range': 'decade'})
        self.assertEqual(res.status_code, 400)

    def test_requires_auth(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class SeriesMcpToolTests(APITestCase):
    """The chart and the agent must report the same numbers."""

    def setUp(self):
        self.user = User.objects.create_user(email='hs4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        HealthDailySummary.objects.create(
            user=self.user, date=dt.date.today(), steps=11000, sleep_hours=7.5)

    def call(self, arguments=None):
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'get_health_series', 'arguments': arguments or {}},
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data['result']

    def test_tool_returns_the_same_shape_as_the_endpoint(self):
        data = self.call({'range': 'daily'})['structuredContent']
        rest = self.client.get(reverse('health-series'), {'range': 'daily'}).data
        self.assertEqual(data['bucket'], rest['bucket'])
        self.assertEqual(len(data['buckets']), len(rest['buckets']))
        self.assertEqual(data['summary']['steps']['latest'],
                         rest['summary']['steps']['latest'])

    def test_bad_range_is_an_error_result(self):
        result = self.call({'range': 'decade'})
        self.assertTrue(result['isError'])
        self.assertIn('range must be one of', result['content'][0]['text'])

    def test_tool_is_read_only(self):
        from api import mcp
        self.assertFalse(mcp.TOOLS['get_health_series']['writes'])
