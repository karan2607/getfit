"""
Health sync: credential handling, cross-user isolation, and payload robustness.

Review findings this covers:

1. The sync token was md5(user_id + "getFit"). The derivation and salt are
   readable in a PUBLIC repo, so the credential was computable from an account
   UUID -- and unrotatable, because save() and health_connect both only filled a
   blank field. The only remedy was DELETE, which wipes all health data.
2. HealthWorkout.terra_workout_id was globally unique and the sync did
   update_or_create() WITHOUT filtering by user, so a client reusing another
   account's workout id reassigned that account's row to itself.
3. `except Exception: data = {}` returned 200 with nothing stored -- an agent
   syncing daily would never learn it had failed.
4. The payload date went straight into a DateField lookup, unvalidated.
5. Regex "JSON repair" over the raw body could corrupt string values.
"""

import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from api.models import (
    User, UserProfile, HealthConnection, HealthDailySummary, HealthWorkout,
)


class SyncTokenTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='h1@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')

    def test_token_is_not_derivable_from_the_user_id(self):
        import hashlib
        conn = HealthConnection.objects.create(user=self.user)
        derivable = hashlib.md5(
            ('%sgetFit' % conn.user_id).encode()).hexdigest()
        self.assertNotEqual(conn.sync_token, derivable)

    def test_token_is_long_and_random(self):
        a = HealthConnection.objects.create(user=self.user)
        other = User.objects.create_user(email='h2@test.local', password='x')
        b = HealthConnection.objects.create(user=other)
        self.assertGreaterEqual(len(a.sync_token), 32)
        self.assertNotEqual(a.sync_token, b.sync_token)

    def test_rotation_invalidates_the_previous_token(self):
        conn = HealthConnection.objects.create(user=self.user)
        old = conn.sync_token
        new = conn.rotate_sync_token()
        self.assertNotEqual(old, new)
        self.assertFalse(
            HealthConnection.objects.filter(sync_token=old).exists())

    def test_rotate_endpoint_requires_auth(self):
        res = self.client.post(reverse('health-connect-rotate'))
        self.assertIn(res.status_code, (401, 403))

    def test_rotate_endpoint_keeps_the_health_data(self):
        """The old workaround (DELETE) wiped everything; rotation must not."""
        from rest_framework.authtoken.models import Token
        conn = HealthConnection.objects.create(user=self.user)
        HealthDailySummary.objects.create(
            user=self.user, date=timezone.now().date(), steps=5000)
        old = conn.sync_token

        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.post(reverse('health-connect-rotate'))

        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotEqual(res.data['token'], old)
        self.assertEqual(
            HealthDailySummary.objects.filter(user=self.user).count(), 1)

    def test_connect_endpoint_does_not_derive_a_token(self):
        import hashlib
        from rest_framework.authtoken.models import Token
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.get(reverse('health-connect'))
        derivable = hashlib.md5(
            ('%sgetFit' % self.user.id).encode()).hexdigest()
        self.assertNotEqual(res.data['token'], derivable)


class SyncAuthTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='h3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.conn = HealthConnection.objects.create(user=self.user)
        self.url = reverse('health-shortcuts-sync')

    def post(self, payload, token=None, raw=None):
        return self.client.post(
            self.url,
            raw if raw is not None else json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION='Sync ' + (token or self.conn.sync_token),
        )

    def test_missing_header_is_401(self):
        res = self.client.post(self.url, '{}', content_type='application/json')
        self.assertEqual(res.status_code, 401)

    def test_bad_token_is_401(self):
        res = self.post({'steps': 1}, token='nope')
        self.assertEqual(res.status_code, 401)

    def test_get_confirms_the_connection(self):
        res = self.client.get(
            self.url, HTTP_AUTHORIZATION='Sync ' + self.conn.sync_token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user'], 'h3@test.local')


class SyncPayloadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='h4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.conn = HealthConnection.objects.create(user=self.user)
        self.url = reverse('health-shortcuts-sync')

    def post(self, payload=None, raw=None):
        return self.client.post(
            self.url,
            raw if raw is not None else json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION='Sync ' + self.conn.sync_token,
        )

    # -- the silent-failure bug ----------------------------------------------

    def test_malformed_payload_is_400_not_a_silent_200(self):
        res = self.post(raw='{this is not json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(HealthDailySummary.objects.count(), 0)

    def test_empty_body_is_400(self):
        self.assertEqual(self.post(raw='').status_code, 400)

    def test_non_object_payload_is_400(self):
        self.assertEqual(self.post(raw='[1,2,3]').status_code, 400)

    # -- the Shortcuts malformation, repaired narrowly -----------------------

    def test_shortcuts_empty_value_is_tolerated(self):
        """Shortcuts emits `"steps": ,` when a HealthKit query returns nothing."""
        res = self.post(raw='{"steps": , "sleep_hours": 7}')
        self.assertEqual(res.status_code, 200, res.data)
        summary = HealthDailySummary.objects.get()
        self.assertIsNone(summary.steps)
        self.assertEqual(summary.sleep_hours, 7)

    def test_repair_does_not_corrupt_string_values(self):
        """
        The old regex ran over the raw body, so an activity_type containing
        ": " before a comma was mangled.
        """
        res = self.post({
            'steps': 1000,
            'workouts': [{'id': 'w1', 'activity_type': 'Run: morning, easy',
                          'start_time': '2026-10-01T07:00:00Z'}],
        })
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(HealthWorkout.objects.get().activity_type,
                         'Run: morning, easy')

    # -- date validation ------------------------------------------------------

    def test_absent_date_defaults_to_today(self):
        res = self.post({'steps': 100})
        self.assertEqual(res.data['date'], timezone.now().date().isoformat())

    def test_malformed_date_is_400_not_500(self):
        res = self.post({'date': 'last tuesday', 'steps': 100})
        self.assertEqual(res.status_code, 400)
        self.assertIn('ISO format', res.data['detail'])

    def test_far_future_date_is_rejected(self):
        res = self.post({'date': '2206-01-01', 'steps': 100})
        self.assertEqual(res.status_code, 400)
        self.assertIn('future', res.data['detail'])

    # -- field coercion -------------------------------------------------------

    def test_thousands_separators_are_handled(self):
        res = self.post({'steps': '12,345'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(HealthDailySummary.objects.get().steps, 12345)

    def test_non_numeric_field_is_reported_not_silently_dropped(self):
        res = self.post({'steps': 'lots', 'sleep_hours': 7})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(any('steps' in p for p in res.data['problems']))
        self.assertEqual(HealthDailySummary.objects.get().sleep_hours, 7)

    def test_out_of_range_values_are_rejected(self):
        res = self.post({'steps': 4000000, 'resting_heart_rate': 900})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(res.data['problems']), 2)
        self.assertEqual(HealthDailySummary.objects.count(), 0)

    def test_partial_payload_does_not_null_existing_values(self):
        self.post({'steps': 5000, 'sleep_hours': 8})
        self.post({'steps': 6000})
        summary = HealthDailySummary.objects.get()
        self.assertEqual(summary.steps, 6000)
        self.assertEqual(summary.sleep_hours, 8)

    # -- response accuracy ----------------------------------------------------

    def test_response_reports_this_call_not_all_time_totals(self):
        """`summaries_stored` used to be the all-time row count."""
        res = self.post({'steps': 5000})
        self.assertEqual(sorted(res.data['fields_stored']), ['steps'])
        self.assertTrue(res.data['summary_created'])
        self.assertEqual(res.data['workouts_stored'], 0)

    def test_last_sync_at_is_updated(self):
        self.assertIsNone(self.conn.last_sync_at)
        self.post({'steps': 1})
        self.conn.refresh_from_db()
        self.assertIsNotNone(self.conn.last_sync_at)

    # -- workouts -------------------------------------------------------------

    def test_workout_is_stored(self):
        res = self.post({'workouts': [{
            'id': 'w-1', 'activity_type': 'Running',
            'start_time': '2026-10-01T07:00:00Z',
            'duration_seconds': 1800, 'calories': 300}]})
        self.assertEqual(res.data['workouts_stored'], 1)
        w = HealthWorkout.objects.get()
        self.assertEqual(w.activity_type, 'Running')
        self.assertEqual(w.duration_seconds, 1800)

    def test_workout_without_start_time_is_reported(self):
        res = self.post({'workouts': [{'id': 'w-1', 'activity_type': 'Run'}]})
        self.assertEqual(res.data['workouts_stored'], 0)
        self.assertTrue(any('start_time' in p for p in res.data['problems']))

    def test_resyncing_the_same_workout_updates_it(self):
        for cals in (300, 350):
            self.post({'workouts': [{
                'id': 'w-1', 'activity_type': 'Running',
                'start_time': '2026-10-01T07:00:00Z', 'calories': cals}]})
        self.assertEqual(HealthWorkout.objects.count(), 1)
        self.assertEqual(HealthWorkout.objects.get().calories, 350)


class CrossUserIsolationTests(APITestCase):
    """
    The data-corruption bug: workout ids were globally unique and the sync
    lookup was not scoped by user.
    """

    def setUp(self):
        self.victim = User.objects.create_user(email='victim@test.local',
                                               password='x')
        UserProfile.objects.create(user=self.victim, preferred_unit='lb')
        self.victim_conn = HealthConnection.objects.create(user=self.victim)

        self.attacker = User.objects.create_user(email='attacker@test.local',
                                                 password='x')
        UserProfile.objects.create(user=self.attacker, preferred_unit='lb')
        self.attacker_conn = HealthConnection.objects.create(user=self.attacker)
        self.url = reverse('health-shortcuts-sync')

    def post_as(self, conn, payload):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json',
            HTTP_AUTHORIZATION='Sync ' + conn.sync_token)

    def test_reusing_another_users_workout_id_does_not_steal_their_row(self):
        shared_id = 'collide-1'
        self.post_as(self.victim_conn, {'workouts': [{
            'id': shared_id, 'activity_type': 'Victim Run',
            'start_time': '2026-10-01T07:00:00Z'}]})
        self.post_as(self.attacker_conn, {'workouts': [{
            'id': shared_id, 'activity_type': 'Attacker Run',
            'start_time': '2026-10-02T07:00:00Z'}]})

        # Two rows, one per user, each with its own data.
        self.assertEqual(HealthWorkout.objects.count(), 2)
        victim_row = HealthWorkout.objects.get(user=self.victim)
        self.assertEqual(victim_row.activity_type, 'Victim Run',
                         "the victim's workout must not be reassigned")
        attacker_row = HealthWorkout.objects.get(user=self.attacker)
        self.assertEqual(attacker_row.activity_type, 'Attacker Run')

    def test_summaries_are_per_user(self):
        today = timezone.now().date().isoformat()
        self.post_as(self.victim_conn, {'date': today, 'steps': 1000})
        self.post_as(self.attacker_conn, {'date': today, 'steps': 9999})
        self.assertEqual(
            HealthDailySummary.objects.get(user=self.victim).steps, 1000)
        self.assertEqual(
            HealthDailySummary.objects.get(user=self.attacker).steps, 9999)

    def test_a_token_only_writes_to_its_own_account(self):
        self.post_as(self.attacker_conn, {'steps': 123})
        self.assertFalse(
            HealthDailySummary.objects.filter(user=self.victim).exists())


class HealthStatusTests(APITestCase):
    def setUp(self):
        from rest_framework.authtoken.models import Token
        self.user = User.objects.create_user(email='hs@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_no_connection_reports_disconnected(self):
        res = self.client.get(reverse('health-status'))
        self.assertFalse(res.data['connected'])
        self.assertFalse(res.data['has_data'])

    def test_fresh_connection_reports_connected_with_no_data(self):
        """
        `connected` used to mean "data has arrived", so a user who had just
        connected looked disconnected -- exactly when they need confirmation.
        """
        HealthConnection.objects.create(user=self.user)
        res = self.client.get(reverse('health-status'))
        self.assertTrue(res.data['connected'])
        self.assertFalse(res.data['has_data'])
        self.assertIsNotNone(res.data['connected_at'])

    def test_connection_with_data_reports_both(self):
        HealthConnection.objects.create(user=self.user)
        HealthDailySummary.objects.create(
            user=self.user, date=timezone.now().date(), steps=1000)
        res = self.client.get(reverse('health-status'))
        self.assertTrue(res.data['connected'])
        self.assertTrue(res.data['has_data'])
        self.assertIsNotNone(res.data['latest_summary_date'])


class StaleDateWarningTests(APITestCase):
    """
    A client sending a STATIC date is invisible without a warning.

    Real case: an Apple Shortcut built on 2026-05-05 captured the date as a
    fixed value, so for five months every sync overwrote one row dated
    2026-05-04. Nothing was ever filed under today, the endpoint reported
    success each time, and the app showed a row that kept updating -- so there
    was no symptom until someone compared the stored date to the clock.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='stale@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.conn = HealthConnection.objects.create(user=self.user)
        self.url = reverse('health-shortcuts-sync')

    def post(self, payload):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json',
            HTTP_AUTHORIZATION='Sync ' + self.conn.sync_token)

    def _old_date(self, days):
        return (timezone.now().date() - timedelta(days=days)).isoformat()

    def test_a_months_old_date_is_flagged(self):
        res = self.post({'date': self._old_date(160), 'steps': 6340})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(any('static date' in p for p in res.data['problems']),
                        msg=res.data['problems'])

    def test_the_warning_is_not_fatal(self):
        """Backfilling an old day is legitimate, so still store the data."""
        day = self._old_date(160)
        res = self.post({'date': day, 'steps': 6340})
        self.assertEqual(res.data['date'], day)
        self.assertEqual(
            HealthDailySummary.objects.get(user=self.user).steps, 6340)

    def test_recent_dates_are_not_flagged(self):
        for days in (0, 1, 3, 6):
            res = self.post({'date': self._old_date(days), 'steps': 100})
            self.assertEqual(res.data['problems'], [],
                             msg='%d days old should not warn' % days)

    def test_omitting_the_date_never_warns(self):
        res = self.post({'steps': 100})
        self.assertEqual(res.data['problems'], [])
        self.assertEqual(res.data['date'], timezone.now().date().isoformat())

    def test_warning_names_the_fix(self):
        res = self.post({'date': self._old_date(160), 'steps': 1})
        warning = [p for p in res.data['problems'] if 'static date' in p][0]
        self.assertIn('Omit the date field', warning)


class DualSourceWriteTests(APITestCase):
    """
    The Shortcut and an MCP agent both writing health data for the same day.

    Two different auth paths (Sync token vs app Token) hitting the same
    HealthDailySummary row. Both use update_or_create with only the fields they
    supplied, so they MERGE per field rather than one wiping the other -- but a
    field sent by both is last-write-wins, which is worth knowing before you
    point two sources at the same metric.
    """

    def setUp(self):
        from rest_framework.authtoken.models import Token
        self.user = User.objects.create_user(email='dual@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.conn = HealthConnection.objects.create(user=self.user)
        self.app_token, _ = Token.objects.get_or_create(user=self.user)
        self.today = timezone.now().date().isoformat()

    def shortcut_sync(self, payload):
        """The Apple Shortcut path: Authorization: Sync <token>."""
        return self.client.post(
            reverse('health-shortcuts-sync'), json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION='Sync ' + self.conn.sync_token)

    def agent_mcp(self, arguments):
        """The agent path: MCP log_health_day with an app token."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.app_token.key)
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'log_health_day', 'arguments': arguments},
        }, format='json')
        self.client.credentials()
        return res

    def summary(self):
        return HealthDailySummary.objects.get(user=self.user)

    def test_both_paths_write_to_the_same_row(self):
        self.shortcut_sync({'date': self.today, 'steps': 8000})
        self.agent_mcp({'date': self.today, 'sleep_hours': 7.5})
        self.assertEqual(HealthDailySummary.objects.count(), 1)

    def test_distinct_fields_merge_rather_than_overwrite(self):
        """The useful case: Shortcut owns steps, agent owns sleep."""
        self.shortcut_sync({'date': self.today, 'steps': 8000,
                            'active_calories': 400})
        self.agent_mcp({'date': self.today, 'sleep_hours': 7.5,
                        'resting_heart_rate': 55})
        s = self.summary()
        self.assertEqual(s.steps, 8000)
        self.assertEqual(s.active_calories, 400)
        self.assertEqual(s.sleep_hours, 7.5)
        self.assertEqual(s.resting_heart_rate, 55)

    def test_order_does_not_matter(self):
        self.agent_mcp({'date': self.today, 'sleep_hours': 7.5})
        self.shortcut_sync({'date': self.today, 'steps': 8000})
        s = self.summary()
        self.assertEqual(s.steps, 8000)
        self.assertEqual(s.sleep_hours, 7.5)

    def test_the_same_field_from_both_sources_is_last_write_wins(self):
        """
        Documented, not prevented: if both sources send `steps`, the later call
        wins. Point one source at each metric to avoid ping-pong.
        """
        self.shortcut_sync({'date': self.today, 'steps': 8000})
        self.agent_mcp({'date': self.today, 'steps': 12000})
        self.assertEqual(self.summary().steps, 12000)

        self.shortcut_sync({'date': self.today, 'steps': 8000})
        self.assertEqual(self.summary().steps, 8000)

    def test_a_stale_dated_shortcut_does_not_touch_todays_row(self):
        """
        The live situation: the Shortcut writes to a fixed old date while the
        agent writes to today. They land on separate rows and do not interfere.
        """
        stale = (timezone.now().date() - timedelta(days=160)).isoformat()
        self.shortcut_sync({'date': stale, 'steps': 6340})
        self.agent_mcp({'date': self.today, 'steps': 11000})

        self.assertEqual(HealthDailySummary.objects.count(), 2)
        self.assertEqual(
            HealthDailySummary.objects.get(date=stale).steps, 6340)
        self.assertEqual(
            HealthDailySummary.objects.get(date=self.today).steps, 11000)

    def test_workouts_from_both_paths_coexist(self):
        self.shortcut_sync({'workouts': [{
            'id': 'shortcut-1', 'activity_type': 'Walk',
            'start_time': '2026-10-14T07:00:00Z'}]})
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.app_token.key)
        self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'log_health_workout', 'arguments': {
                'external_id': 'agent-1', 'activity_type': 'Run',
                'start_time': '2026-10-14T18:00:00Z'}},
        }, format='json')
        self.client.credentials()

        self.assertEqual(HealthWorkout.objects.filter(user=self.user).count(), 2)

    def test_same_workout_id_from_both_paths_updates_one_row(self):
        """Dedupe is by (user, external_id), so the source does not matter."""
        self.shortcut_sync({'workouts': [{
            'id': 'shared', 'activity_type': 'Walk',
            'start_time': '2026-10-14T07:00:00Z', 'calories': 100}]})
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.app_token.key)
        self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'log_health_workout', 'arguments': {
                'external_id': 'shared', 'activity_type': 'Walk',
                'start_time': '2026-10-14T07:00:00Z', 'calories': 250}},
        }, format='json')
        self.client.credentials()

        self.assertEqual(HealthWorkout.objects.filter(user=self.user).count(), 1)
        self.assertEqual(HealthWorkout.objects.get(user=self.user).calories, 250)
