"""
Correcting health data.

Reported: "Let's allow updates to health endpoint. Sometimes wrong data is
being sent which needs correction."

The gap was precise. `_coerce_health_fields` skipped any null, so a wrong
number could be overwritten but **never cleared**, and there was no way to
delete a bad day or a bogus workout row at all.

The fix turns on one distinction, and **the most important test in this file is
that the distinction holds**: the SYNC path must keep ignoring nulls, because a
partial Shortcut payload that sends only steps must not blank out sleep and
heart rate. Correction is a different intent and gets a different endpoint.
"""

import datetime as dt

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.views import _coerce_health_fields
from api.models import (
    User, UserProfile, HealthDailySummary, HealthWorkout, HealthConnection,
)

DAY = dt.date(2026, 10, 20)


class CoercionSentinelTests(APITestCase):
    """absent vs explicit null, the mechanism everything else rests on."""

    def test_sync_ignores_an_explicit_null(self):
        """The regression that would matter most."""
        stored, problems = _coerce_health_fields({'steps': 5000, 'sleep_hours': None})
        self.assertEqual(stored, {'steps': 5000})
        self.assertEqual(problems, [])

    def test_edit_clears_on_an_explicit_null(self):
        stored, problems = _coerce_health_fields(
            {'sleep_hours': None}, allow_clear=True)
        self.assertEqual(stored, {'sleep_hours': None})
        self.assertEqual(problems, [])

    def test_an_absent_key_is_untouched_either_way(self):
        for allow in (False, True):
            stored, _problems = _coerce_health_fields({'steps': 1}, allow_clear=allow)
            self.assertEqual(list(stored), ['steps'])

    def test_an_empty_string_is_treated_as_not_supplied_on_sync(self):
        """Shortcuts send "" for an unset variable constantly."""
        stored, problems = _coerce_health_fields({'steps': '', 'active_calories': 300})
        self.assertEqual(stored, {'active_calories': 300.0})
        self.assertEqual(problems, [])

    def test_range_checks_still_apply_when_clearing_is_allowed(self):
        _stored, problems = _coerce_health_fields(
            {'resting_heart_rate': 500}, allow_clear=True)
        self.assertTrue(problems)

    def test_a_non_number_is_still_rejected(self):
        _stored, problems = _coerce_health_fields(
            {'steps': 'lots'}, allow_clear=True)
        self.assertTrue(problems)


class SummaryEditTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='he@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.summary = HealthDailySummary.objects.create(
            user=self.user, date=DAY, steps=48000, active_calories=344,
            resting_calories=1771, resting_heart_rate=69, sleep_hours=7.5)
        self.url = reverse('health-summary-detail', args=[DAY.isoformat()])

    def test_a_wrong_value_can_be_overwritten(self):
        res = self.client.patch(self.url, {'steps': 4800}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.steps, 4800)

    def test_a_wrong_value_can_be_CLEARED(self):
        """The thing that was impossible before."""
        res = self.client.patch(self.url, {'steps': None}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.summary.refresh_from_db()
        self.assertIsNone(self.summary.steps)
        self.assertEqual(res.data['cleared'], ['steps'])

    def test_untouched_fields_survive_a_patch(self):
        self.client.patch(self.url, {'steps': None}, format='json')
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.sleep_hours, 7.5)
        self.assertEqual(self.summary.resting_heart_rate, 69)

    def test_several_fields_at_once(self):
        res = self.client.patch(
            self.url, {'steps': 4800, 'sleep_hours': None}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.steps, 4800)
        self.assertIsNone(self.summary.sleep_hours)

    def test_an_empty_body_is_a_400(self):
        res = self.client.patch(self.url, {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Nothing to update', res.data['detail'])

    def test_an_implausible_value_is_rejected(self):
        res = self.client.patch(self.url, {'resting_heart_rate': 500},
                                format='json')
        self.assertEqual(res.status_code, 400)
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.resting_heart_rate, 69)

    def test_a_malformed_date_is_a_400(self):
        res = self.client.patch(
            reverse('health-summary-detail', args=['not-a-date']),
            {'steps': 1}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_day_with_no_data_is_a_404(self):
        res = self.client.patch(
            reverse('health-summary-detail', args=['2020-01-01']),
            {'steps': 1}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_delete_removes_the_day(self):
        res = self.client.delete(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(HealthDailySummary.objects.filter(
            user=self.user, date=DAY).exists())

    def test_delete_does_not_touch_neighbouring_days(self):
        HealthDailySummary.objects.create(
            user=self.user, date=DAY - dt.timedelta(days=1), steps=6000)
        HealthDailySummary.objects.create(
            user=self.user, date=DAY + dt.timedelta(days=1), steps=7000)
        self.client.delete(self.url)
        self.assertEqual(
            HealthDailySummary.objects.filter(user=self.user).count(), 2)

    def test_requires_auth(self):
        self.client.credentials()
        self.assertIn(self.client.delete(self.url).status_code, (401, 403))


class CrossUserTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create_user(email='he2a@test.local', password='x')
        self.b = User.objects.create_user(email='he2b@test.local', password='x')
        for user in (self.a, self.b):
            UserProfile.objects.create(user=user, preferred_unit='lb')
        HealthDailySummary.objects.create(user=self.b, date=DAY, steps=9999)
        self.b_workout = HealthWorkout.objects.create(
            user=self.b, terra_workout_id='theirs', activity_type='running',
            start_time=dt.datetime(2026, 10, 20, 7, tzinfo=dt.timezone.utc))
        token, _ = Token.objects.get_or_create(user=self.a)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_another_users_day_is_a_404(self):
        res = self.client.patch(
            reverse('health-summary-detail', args=[DAY.isoformat()]),
            {'steps': 0}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(
            HealthDailySummary.objects.get(user=self.b, date=DAY).steps, 9999)

    def test_another_users_day_cannot_be_deleted(self):
        res = self.client.delete(
            reverse('health-summary-detail', args=[DAY.isoformat()]))
        self.assertEqual(res.status_code, 404)
        self.assertTrue(HealthDailySummary.objects.filter(user=self.b).exists())

    def test_another_users_workout_cannot_be_deleted(self):
        res = self.client.delete(
            reverse('health-workout-detail', args=[self.b_workout.id]))
        self.assertEqual(res.status_code, 404)
        self.assertTrue(HealthWorkout.objects.filter(pk=self.b_workout.pk).exists())


class WorkoutDeleteTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='he3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.workout = HealthWorkout.objects.create(
            user=self.user, terra_workout_id='42', activity_type='walking',
            start_time=dt.datetime(2026, 10, 20, 7, tzinfo=dt.timezone.utc),
            duration_seconds=600)

    def test_delete_removes_it(self):
        res = self.client.delete(
            reverse('health-workout-detail', args=[self.workout.id]))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(HealthWorkout.objects.filter(pk=self.workout.pk).exists())

    def test_an_unknown_id_is_a_404(self):
        res = self.client.delete(
            reverse('health-workout-detail', args=[self.workout.id + 999]))
        self.assertEqual(res.status_code, 404)


class SyncStillCannotWipeTests(APITestCase):
    """
    The regression guard. Correction must not have loosened sync.

    An Apple Shortcut sending only steps must leave sleep and heart rate
    exactly where they were -- that behaviour is deliberate and predates this
    change.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='he4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.conn = HealthConnection.objects.create(
            user=self.user, provider='APPLE')
        HealthDailySummary.objects.create(
            user=self.user, date=DAY, steps=5000, sleep_hours=7.5,
            resting_heart_rate=69)

    def sync(self, payload):
        return self.client.post(
            reverse('health-shortcuts-sync'), payload, format='json',
            HTTP_AUTHORIZATION='Sync ' + self.conn.sync_token)

    def test_a_partial_sync_leaves_other_fields_alone(self):
        res = self.sync({'date': DAY.isoformat(), 'steps': 9000})
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
        summary = HealthDailySummary.objects.get(user=self.user, date=DAY)
        self.assertEqual(summary.steps, 9000)
        self.assertEqual(summary.sleep_hours, 7.5)
        self.assertEqual(summary.resting_heart_rate, 69)

    def test_a_sync_with_explicit_nulls_does_not_clear(self):
        """The whole reason the two paths are separate."""
        res = self.sync({'date': DAY.isoformat(), 'steps': 9000,
                         'sleep_hours': None, 'resting_heart_rate': None})
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
        summary = HealthDailySummary.objects.get(user=self.user, date=DAY)
        self.assertEqual(summary.sleep_hours, 7.5)
        self.assertEqual(summary.resting_heart_rate, 69)

    def test_a_sync_with_empty_strings_does_not_clear(self):
        res = self.sync({'date': DAY.isoformat(), 'steps': 9000,
                         'sleep_hours': '', 'resting_heart_rate': ''})
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
        summary = HealthDailySummary.objects.get(user=self.user, date=DAY)
        self.assertEqual(summary.sleep_hours, 7.5)


class McpCorrectionToolTests(APITestCase):
    """The agent is where the wrong data comes from, so it gets the tools."""

    def setUp(self):
        self.user = User.objects.create_user(email='he5@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        HealthDailySummary.objects.create(
            user=self.user, date=DAY, steps=48000, sleep_hours=7.5)
        HealthWorkout.objects.create(
            user=self.user, terra_workout_id='99', activity_type='walking',
            start_time=dt.datetime(2026, 10, 20, 7, tzinfo=dt.timezone.utc))

    def call(self, name, arguments):
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data['result']

    def test_update_clears_a_field(self):
        result = self.call('update_health_day',
                           {'date': DAY.isoformat(), 'steps': None})
        self.assertFalse(result.get('isError'), result)
        self.assertEqual(result['structuredContent']['cleared'], ['steps'])
        self.assertIsNone(HealthDailySummary.objects.get(
            user=self.user, date=DAY).steps)

    def test_update_leaves_other_fields_alone(self):
        self.call('update_health_day', {'date': DAY.isoformat(), 'steps': 4800})
        self.assertEqual(HealthDailySummary.objects.get(
            user=self.user, date=DAY).sleep_hours, 7.5)

    def test_update_on_a_missing_day_is_an_error(self):
        result = self.call('update_health_day',
                           {'date': '2020-01-01', 'steps': 1})
        self.assertTrue(result['isError'])

    def test_update_with_no_fields_is_an_error(self):
        result = self.call('update_health_day', {'date': DAY.isoformat()})
        self.assertTrue(result['isError'])

    def test_delete_day_removes_it(self):
        result = self.call('delete_health_day', {'date': DAY.isoformat()})
        self.assertFalse(result.get('isError'), result)
        self.assertFalse(HealthDailySummary.objects.filter(
            user=self.user, date=DAY).exists())

    def test_delete_workout_by_external_id(self):
        result = self.call('delete_health_workout', {'external_id': '99'})
        self.assertFalse(result.get('isError'), result)
        self.assertFalse(HealthWorkout.objects.filter(user=self.user).exists())

    def test_delete_workout_requires_an_id(self):
        self.assertTrue(self.call('delete_health_workout', {})['isError'])

    def test_another_users_workout_is_not_deletable(self):
        """terra_workout_id is unique per USER, so the filter is load-bearing."""
        other = User.objects.create_user(email='he6@test.local', password='x')
        UserProfile.objects.create(user=other, preferred_unit='lb')
        HealthWorkout.objects.create(
            user=other, terra_workout_id='mine-not-yours',
            activity_type='running',
            start_time=dt.datetime(2026, 10, 20, 8, tzinfo=dt.timezone.utc))
        result = self.call('delete_health_workout',
                           {'external_id': 'mine-not-yours'})
        self.assertTrue(result['isError'])
        self.assertTrue(HealthWorkout.objects.filter(user=other).exists())

    def test_the_correction_tools_are_marked_as_writes(self):
        from api import mcp
        for name in ('update_health_day', 'delete_health_day',
                     'delete_health_workout'):
            self.assertTrue(mcp.TOOLS[name]['writes'], msg=name)
