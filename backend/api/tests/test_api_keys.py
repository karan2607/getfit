"""
Long-lived integration keys.

The bug this exists to fix: DRF models `Token` as ONE ROW PER USER, so the
browser session and the user's phone agent shared a single credential, and
`logout()` deletes it. The web app auto-logs-out after 30 minutes of inactivity
and calls that endpoint, so leaving a tab open silently revoked the agent's
access. Reported as "my token to send health metrics has expired" — DRF tokens
have no expiry; the row was being deleted and a different one minted.

This is new authentication surface, so the tests are weighted towards what must
NOT be possible: no cross-user access, no privilege escalation, no managing
keys with a key, and no reaching anything outside the declared scope.
"""

import datetime as dt
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.auth_keys import scope_allows
from api.models import (
    ApiKey, User, UserProfile, WorkoutPlan, WorkoutDay, Exercise,
    WorkoutSession, MealLog,
)


class ApiKeyModelTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ak@test.local', password='x')

    def test_a_key_is_generated_on_save(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        self.assertEqual(len(key.key), 43)          # token_urlsafe(32)
        self.assertTrue(key.is_active)

    def test_keys_are_unique_and_unguessable(self):
        keys = {ApiKey.objects.create(user=self.user, label='k%d' % i).key
                for i in range(20)}
        self.assertEqual(len(keys), 20)

    def test_the_key_is_not_derived_from_the_user(self):
        """
        The original sync token was md5(user_id + "getFit") in a public repo,
        which migration 0034 had to replace. Do not repeat it.
        """
        other = User.objects.create_user(email='ak2@test.local', password='x')
        a = ApiKey.objects.create(user=self.user, label='a')
        b = ApiKey.objects.create(user=other, label='b')
        self.assertNotEqual(a.key, b.key)
        self.assertNotIn(str(self.user.id), a.key)

    def test_prefix_does_not_reveal_the_key(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        self.assertEqual(len(key.prefix), 8)
        self.assertTrue(key.key.startswith(key.prefix))

    def test_touch_records_use(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        self.assertIsNone(key.last_used_at)
        self.assertTrue(key.touch())
        self.assertIsNotNone(key.last_used_at)

    def test_touch_is_throttled(self):
        """An all-day agent must not write this row on every request."""
        key = ApiKey.objects.create(user=self.user, label='phone')
        key.touch()
        first = key.last_used_at
        self.assertFalse(key.touch())
        self.assertEqual(key.last_used_at, first)

    def test_touch_writes_again_after_the_interval(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        ApiKey.objects.filter(pk=key.pk).update(
            last_used_at=timezone.now() - dt.timedelta(hours=2))
        key.refresh_from_db()
        self.assertTrue(key.touch())


class BearerAuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ak3@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.key = ApiKey.objects.create(user=self.user, label='phone agent')
        self.url = reverse('health-summary')

    def bearer(self, value):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + value)

    def test_a_valid_key_authenticates(self):
        self.bearer(self.key.key)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_an_unknown_key_is_rejected(self):
        self.bearer('nope' * 10)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_a_revoked_key_is_rejected(self):
        self.key.revoked_at = timezone.now()
        self.key.save(update_fields=['revoked_at'])
        self.bearer(self.key.key)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_revoked_and_unknown_are_indistinguishable(self):
        """The response must not confirm that a key once existed."""
        self.bearer('nope' * 10)
        unknown = self.client.get(self.url).data
        self.key.revoked_at = timezone.now()
        self.key.save(update_fields=['revoked_at'])
        self.bearer(self.key.key)
        self.assertEqual(self.client.get(self.url).data, unknown)

    def test_an_inactive_user_is_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.bearer(self.key.key)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_a_malformed_header_is_rejected_not_crashed(self):
        for value in ('', ' ', 'a b c'):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + value)
            self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_no_header_is_still_unauthenticated(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_using_the_key_stamps_last_used(self):
        self.bearer(self.key.key)
        self.client.get(self.url)
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_the_session_token_still_works(self):
        """Nothing about the web app may change."""
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(self.client.get(self.url).status_code, 200)


class SessionIndependenceTests(APITestCase):
    """The actual bug: a browser logout must not touch the agent's key."""

    def setUp(self):
        self.user = User.objects.create_user(email='ak4@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.key = ApiKey.objects.create(user=self.user, label='phone agent')

    def test_logout_does_not_revoke_the_integration_key(self):
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(self.client.post(reverse('logout')).status_code, 200)

        # The session token is gone, exactly as before...
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        # ...and the integration key still works.
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key.key)
        self.assertEqual(
            self.client.get(reverse('health-summary')).status_code, 200)

    def test_a_password_change_does_not_revoke_the_integration_key(self):
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.post(reverse('password-change'), {
            'current_password': 'x', 'new_password': 'a-new-password-123',
        }, format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', res.content))

        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key.key)
        self.assertEqual(
            self.client.get(reverse('health-summary')).status_code, 200)

    def test_a_full_login_logout_cycle_leaves_the_key_alone(self):
        for _ in range(3):
            # Clear first: a stale Token header makes even AllowAny endpoints
            # 401, since authentication runs before permissions.
            self.client.credentials()
            res = self.client.post(reverse('login'), {
                'email': 'ak4@test.local', 'password': 'x'}, format='json')
            self.assertEqual(res.status_code, 200)
            self.client.credentials(
                HTTP_AUTHORIZATION='Token ' + res.data['token'])
            self.client.post(reverse('logout'))
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)


class ScopeTests(APITestCase):
    """`logging` scope: what it can and must not reach."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            email='ak5@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.key = ApiKey.objects.create(user=self.user, label='phone')
        self.full = ApiKey.objects.create(user=self.user, label='full',
                                          scope=ApiKey.SCOPE_FULL)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key.key)

    # -- allowed -------------------------------------------------------------

    def test_health_reads_and_writes_are_allowed(self):
        self.assertEqual(
            self.client.get(reverse('health-summary')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('health-series')).status_code, 200)

    def test_meal_logging_is_allowed(self):
        res = self.client.post(reverse('meal-logs'), {
            'date': dt.date.today().isoformat(), 'meal_type': 'lunch',
            'food_name': 'rice', 'calories': 400,
        }, format='json')
        self.assertIn(res.status_code, (200, 201), getattr(res, 'data', None))
        self.assertEqual(MealLog.objects.filter(user=self.user).count(), 1)

    def test_reading_plans_and_the_profile_is_allowed(self):
        self.assertEqual(
            self.client.get(reverse('workout-plans')).status_code, 200)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)

    def test_mcp_is_allowed(self):
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {},
        }, format='json')
        self.assertEqual(res.status_code, 200)

    # -- denied --------------------------------------------------------------

    def test_a_logging_key_cannot_change_the_password(self):
        """The escalation that matters: a leaked key must not take the account."""
        res = self.client.post(reverse('password-change'), {
            'current_password': 'x', 'new_password': 'attacker-chosen-123',
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(
            User.objects.get(pk=self.user.pk).check_password('x'))

    def test_a_logging_key_cannot_reach_the_admin_endpoints(self):
        """Even though this user IS a superuser."""
        self.assertEqual(
            self.client.get(reverse('admin-users')).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('admin-reset-link'),
                             {'email': 'ak5@test.local'},
                             format='json').status_code, 403)

    def test_a_logging_key_cannot_submit_feedback(self):
        self.assertEqual(
            self.client.post(reverse('feedback'), {'body': 'x'},
                             format='json').status_code, 403)

    def test_a_logging_key_cannot_write_the_profile(self):
        res = self.client.patch(reverse('profile'),
                                {'preferred_unit': 'kg'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_logging_key_cannot_generate_a_plan(self):
        """Expensive and not something a logging agent should trigger."""
        res = self.client.post(reverse('workout-plan-generate'),
                               {'days_per_week': 3, 'duration_weeks': 4},
                               format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_logging_key_cannot_delete_a_plan(self):
        plan = WorkoutPlan.objects.create(user=self.user, title='P',
                                          current_week=1)
        res = self.client.delete(
            reverse('workout-plan-detail', args=[plan.id]))
        self.assertEqual(res.status_code, 403)
        self.assertTrue(WorkoutPlan.objects.filter(pk=plan.pk).exists())

    def test_a_logging_key_cannot_log_out_the_session(self):
        self.assertEqual(self.client.post(reverse('logout')).status_code, 403)

    # -- full scope ----------------------------------------------------------

    def test_a_full_scope_key_is_not_restricted(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.full.key)
        res = self.client.patch(reverse('profile'),
                                {'preferred_unit': 'kg'}, format='json')
        self.assertEqual(res.status_code, 200)

    # -- the pure function ---------------------------------------------------

    def test_scope_allows_denies_unlisted_paths_by_default(self):
        """
        Allow-list, not deny-list: an endpoint added tomorrow is closed to
        integration keys until someone lists it on purpose.
        """
        for path in ('/api/auth/login/', '/api/some-new-endpoint/',
                     '/api/feedback/', '/api/auth/admin/users/'):
            self.assertFalse(scope_allows(self.key, path, 'GET'), msg=path)

    def test_scope_allows_passes_non_key_auth_through(self):
        self.assertTrue(scope_allows(None, '/api/auth/admin/users/', 'GET'))


class CrossUserIsolationTests(APITestCase):
    """Mirrors CrossUserIsolationTests in test_mcp.py."""

    def setUp(self):
        self.a = User.objects.create_user(email='ak6a@test.local', password='x')
        self.b = User.objects.create_user(email='ak6b@test.local', password='x')
        for user in (self.a, self.b):
            UserProfile.objects.create(user=user, preferred_unit='lb')
        self.key_a = ApiKey.objects.create(user=self.a, label='a')
        MealLog.objects.create(user=self.b, date=dt.date.today(),
                              meal_type='lunch', food_name='theirs',
                              calories=999)

    def test_a_key_only_sees_its_own_users_data(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key_a.key)
        res = self.client.get(reverse('meal-logs'), {'date': dt.date.today()})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])

    def test_a_key_cannot_revoke_another_users_key(self):
        key_b = ApiKey.objects.create(user=self.b, label='b')
        token, _ = Token.objects.get_or_create(user=self.a)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.delete(
            reverse('api-key-revoke', args=[key_b.id]))
        self.assertEqual(res.status_code, 404)
        key_b.refresh_from_db()
        self.assertTrue(key_b.is_active)

    def test_writes_land_on_the_keys_own_user(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key_a.key)
        self.client.post(reverse('meal-logs'), {
            'date': dt.date.today().isoformat(), 'meal_type': 'dinner',
            'food_name': 'mine', 'calories': 500,
        }, format='json')
        self.assertTrue(MealLog.objects.filter(user=self.a).exists())
        self.assertEqual(MealLog.objects.filter(user=self.b).count(), 1)


class ManagementEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ak7@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        self.url = reverse('api-keys')

    def test_create_returns_the_secret_exactly_once(self):
        res = self.client.post(self.url, {'label': 'phone agent'},
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn('key', res.data)
        self.assertEqual(len(res.data['key']), 43)

        listed = self.client.get(self.url).data
        self.assertEqual(len(listed), 1)
        self.assertNotIn('key', listed[0])
        self.assertIn('prefix', listed[0])

    def test_create_defaults_to_the_logging_scope(self):
        res = self.client.post(self.url, {'label': 'phone'}, format='json')
        self.assertEqual(res.data['scope'], ApiKey.SCOPE_LOGGING)

    def test_label_is_required(self):
        for body in ({}, {'label': ''}, {'label': '   '}):
            self.assertEqual(
                self.client.post(self.url, body, format='json').status_code,
                400)

    def test_an_unknown_scope_is_rejected(self):
        res = self.client.post(self.url, {'label': 'x', 'scope': 'everything'},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_revoke_is_a_soft_delete(self):
        created = self.client.post(self.url, {'label': 'phone'},
                                   format='json').data
        res = self.client.delete(
            reverse('api-key-revoke', args=[created['id']]))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['is_active'])
        # The row survives, so last_used_at is still available for diagnosis.
        self.assertTrue(ApiKey.objects.filter(pk=created['id']).exists())

    def test_revoking_twice_is_harmless(self):
        created = self.client.post(self.url, {'label': 'phone'},
                                   format='json').data
        url = reverse('api-key-revoke', args=[created['id']])
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 200)

    def test_an_integration_key_cannot_mint_another_key(self):
        """
        The escalation path worth closing explicitly: a scoped key issuing
        itself an unscoped one.
        """
        key = ApiKey.objects.create(user=self.user, label='phone')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + key.key)
        self.assertEqual(
            self.client.post(self.url, {'label': 'escalated'},
                             format='json').status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_an_integration_key_cannot_revoke_a_key(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        other = ApiKey.objects.create(user=self.user, label='other')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + key.key)
        res = self.client.delete(reverse('api-key-revoke', args=[other.id]))
        self.assertEqual(res.status_code, 403)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_there_is_a_cap_on_active_keys(self):
        from api.views import API_KEY_LIMIT
        for i in range(API_KEY_LIMIT):
            ApiKey.objects.create(user=self.user, label='k%d' % i)
        res = self.client.post(self.url, {'label': 'one too many'},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_revoking_frees_a_slot(self):
        from api.views import API_KEY_LIMIT
        keys = [ApiKey.objects.create(user=self.user, label='k%d' % i)
                for i in range(API_KEY_LIMIT)]
        self.client.delete(reverse('api-key-revoke', args=[keys[0].id]))
        self.assertEqual(
            self.client.post(self.url, {'label': 'ok now'},
                             format='json').status_code, 201)

    def test_requires_authentication(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_last_used_is_reported(self):
        key = ApiKey.objects.create(user=self.user, label='phone')
        key.touch()
        listed = self.client.get(self.url).data
        row = next(r for r in listed if r['id'] == str(key.id))
        self.assertIsNotNone(row['last_used_at'])


class FeedbackScopeTests(APITestCase):
    """
    The scope for the autonomous queue runner.

    The runner previously authenticated with a DRF session token, which
    `logout()` deletes — so the web app's 30-minute idle timeout silently
    killed it, the same failure as the phone agent. It needs the feedback
    endpoints and nothing else, so `logging` (which denies feedback) and `full`
    (which grants everything) were both wrong.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            email='fb@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.key = ApiKey.objects.create(
            user=self.user, label='queue runner', scope=ApiKey.SCOPE_FEEDBACK)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key.key)

    def test_it_can_read_the_queue(self):
        self.assertEqual(self.client.get(reverse('feedback')).status_code, 200)

    def test_it_can_write_back_a_status(self):
        res = self.client.post(reverse('feedback'), {'body': 'from a test'},
                               format='json')
        self.assertIn(res.status_code, (200, 201), getattr(res, 'data', None))
        item_id = res.data['id']
        res = self.client.patch(reverse('feedback-detail', args=[item_id]),
                                {'status': 'done'}, format='json')
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))

    def test_it_cannot_reach_health(self):
        self.assertEqual(
            self.client.get(reverse('health-summary')).status_code, 403)

    def test_it_cannot_reach_workouts_or_diet(self):
        self.assertEqual(
            self.client.get(reverse('workout-plans')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('meal-logs')).status_code, 403)

    def test_it_cannot_change_the_password(self):
        """Least privilege: a key in a plaintext file on a laptop."""
        res = self.client.post(reverse('password-change'), {
            'current_password': 'x', 'new_password': 'attacker-chosen-123',
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.get(pk=self.user.pk).check_password('x'))

    def test_it_cannot_reach_the_admin_endpoints(self):
        self.assertEqual(
            self.client.get(reverse('admin-users')).status_code, 403)

    def test_it_cannot_reach_mcp(self):
        res = self.client.post(reverse('mcp'), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}},
            format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_logging_key_still_cannot_reach_feedback(self):
        """The two scopes must not have leaked into each other."""
        logging_key = ApiKey.objects.create(
            user=self.user, label='phone', scope=ApiKey.SCOPE_LOGGING)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + logging_key.key)
        self.assertEqual(self.client.get(reverse('feedback')).status_code, 403)

    def test_the_scope_is_still_gated_by_the_superuser_check(self):
        """
        The scope widens nothing on its own — the feedback views enforce
        superuser themselves.
        """
        plain = User.objects.create_user(email='fb2@test.local', password='x')
        UserProfile.objects.create(user=plain, preferred_unit='lb')
        key = ApiKey.objects.create(user=plain, label='runner',
                                    scope=ApiKey.SCOPE_FEEDBACK)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + key.key)
        self.assertEqual(self.client.get(reverse('feedback')).status_code, 403)

    def test_the_scope_is_accepted_at_creation(self):
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.post(reverse('api-keys'),
                               {'label': 'runner', 'scope': 'feedback'},
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['scope'], ApiKey.SCOPE_FEEDBACK)


class ReadonlyScopeTests(APITestCase):
    """
    The `readonly` scope.

    Why it exists: the feedback runner escalated an item in order to protect
    22-rep bench sets the user had ALREADY corrected. All it had was the
    submission text and a feedback-scoped key, so it could not check whether
    the claim about the data was still true. This scope lets an unattended
    agent look, and nothing else.

    New authentication surface, so these are weighted towards what must NOT be
    possible.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='ro@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.key = ApiKey.objects.create(
            user=self.user, label='verifier', scope=ApiKey.SCOPE_READONLY)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.key.key)

        self.plan = WorkoutPlan.objects.create(
            user=self.user, title='Mine', current_week=1)
        self.day = WorkoutDay.objects.create(
            plan=self.plan, day_number=1, week_number=1, name='Push')

    # -- what it CAN do -----------------------------------------------------

    def test_it_can_read_its_own_plans(self):
        """The capability the escalation needed and did not have."""
        res = self.client.get(reverse('workout-plans'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p['title'] for p in res.data], ['Mine'])

    def test_it_can_read_a_plan_detail_including_exercises(self):
        Exercise.objects.create(day=self.day, name='Push-ups', sets=3,
                                reps='15')
        res = self.client.get(
            reverse('workout-plan-detail', args=[self.plan.id]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            [e['name'] for e in res.data['days'][0]['exercises']],
            ['Push-ups'])

    def test_it_can_read_the_profile(self):
        res = self.client.get(reverse('profile'))
        self.assertEqual(res.status_code, 200)

    # -- what it must NOT do ------------------------------------------------

    def test_it_cannot_write_anything(self):
        """
        The defining property. A write of ANY shape must fail, which is what
        makes this safe to hand to an unattended agent.
        """
        cases = [
            ('post', reverse('workout-plans'), {'title': 'New'}),
            ('patch', reverse('profile'), {'preferred_unit': 'kg'}),
            ('delete', reverse('workout-plan-detail', args=[self.plan.id]), None),
            ('put', reverse('workout-plan-detail', args=[self.plan.id]),
             {'title': 'Renamed', 'days': []}),
        ]
        for method, url, body in cases:
            call = getattr(self.client, method)
            res = call(url, body, format='json') if body else call(url)
            self.assertEqual(res.status_code, 403,
                             msg='%s %s should be denied' % (method.upper(), url))
        # ...and nothing actually changed.
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.title, 'Mine')
        self.assertEqual(WorkoutPlan.objects.filter(user=self.user).count(), 1)

    def test_it_cannot_log_health_data(self):
        """Narrower than `logging` on purpose: that scope writes, this reads."""
        res = self.client.post(reverse('health-summary'),
                               {'date': '2026-10-28', 'steps': 1000},
                               format='json')
        self.assertEqual(res.status_code, 403)

    def test_it_cannot_reach_an_unlisted_endpoint(self):
        """Allow-list: an endpoint added tomorrow is closed until listed."""
        res = self.client.get(reverse('api-keys'))
        self.assertEqual(res.status_code, 403)

    def test_it_cannot_generate_a_plan(self):
        """Expensive, and a write however it is framed."""
        res = self.client.post(reverse('workout-plan-generate'),
                               {'days_per_week': 3, 'duration_weeks': 4},
                               format='json')
        self.assertEqual(res.status_code, 403)

    def test_it_cannot_change_the_password(self):
        res = self.client.post(reverse('password-change'),
                               {'current_password': 'x',
                                'new_password': 'yyyyyyyyyy1'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_it_cannot_see_another_users_data(self):
        """
        A scope decides which ENDPOINTS are reachable, never whose rows. Every
        view filters on request.user, and this is the test that says so.
        """
        other = User.objects.create_user(email='ro2@test.local', password='x')
        other_plan = WorkoutPlan.objects.create(
            user=other, title='Theirs', current_week=1)

        res = self.client.get(reverse('workout-plans'))
        self.assertEqual([p['title'] for p in res.data], ['Mine'])

        res = self.client.get(
            reverse('workout-plan-detail', args=[other_plan.id]))
        self.assertEqual(res.status_code, 404)

    def test_a_revoked_readonly_key_cannot_read(self):
        self.key.revoked_at = timezone.now()
        self.key.save(update_fields=['revoked_at'])
        res = self.client.get(reverse('workout-plans'))
        self.assertEqual(res.status_code, 401)

    # -- it does not disturb the other scopes -------------------------------

    def test_the_other_scopes_are_unchanged(self):
        """
        A regression guard. Adding a scope must not quietly re-open or close
        anything for the keys already in use.
        """
        logging_key = ApiKey.objects.create(
            user=self.user, label='phone', scope=ApiKey.SCOPE_LOGGING)
        feedback_key = ApiKey.objects.create(
            user=self.user, label='loop', scope=ApiKey.SCOPE_FEEDBACK)

        # logging still writes health, still cannot reach feedback.
        self.assertTrue(scope_allows(logging_key, '/api/health/sync/', 'POST'))
        self.assertFalse(scope_allows(logging_key, '/api/feedback/', 'GET'))
        # feedback still reaches only the queue.
        self.assertTrue(scope_allows(feedback_key, '/api/feedback/', 'GET'))
        self.assertFalse(scope_allows(feedback_key, '/api/workouts/plans/', 'GET'))
        # readonly reads workouts but writes nothing.
        self.assertTrue(scope_allows(self.key, '/api/workouts/plans/', 'GET'))
        self.assertFalse(scope_allows(self.key, '/api/workouts/plans/', 'POST'))
        self.assertFalse(scope_allows(self.key, '/api/feedback/', 'GET'))

    def test_every_unsafe_method_is_denied_across_every_allowed_prefix(self):
        """
        Exhaustive rather than representative: the allow-list is the security
        boundary, so "safe methods only" is asserted over the whole cross
        product instead of a sample.
        """
        from api.auth_keys import READONLY_SCOPE_ALLOWED
        for prefix in READONLY_SCOPE_ALLOWED:
            path = '/api/' + prefix
            for method in ('GET', 'HEAD', 'OPTIONS'):
                self.assertTrue(scope_allows(self.key, path, method),
                                msg='%s %s' % (method, path))
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                self.assertFalse(scope_allows(self.key, path, method),
                                 msg='%s %s' % (method, path))
