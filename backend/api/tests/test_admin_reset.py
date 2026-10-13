"""
Tests for superuser-issued password reset links.

This exists because there is no outbound email configured, so the self-service
flow cannot deliver its link. The operator issues it instead. The critical
property is that the verification step is *moved*, not removed: only a superuser
can mint a link, and the link is still single-use and expiring.
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api.models import User, UserProfile, PasswordResetToken


class AdminResetAccessControlTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin@test.local',
                                                   password='x', name='Admin')
        UserProfile.objects.create(user=self.admin, preferred_unit='lb')
        self.target = User.objects.create_user(email='target@test.local',
                                               password='oldpass', name='Target')
        UserProfile.objects.create(user=self.target, preferred_unit='lb')
        self.attacker = User.objects.create_user(email='attacker@test.local',
                                                 password='x', name='Attacker')
        UserProfile.objects.create(user=self.attacker, preferred_unit='lb')
        self.url = reverse('admin-reset-link')
        self.users_url = reverse('admin-users')

    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_ordinary_user_cannot_issue_a_reset_for_someone_else(self):
        """The whole point: this must not become an account-takeover primitive."""
        self.auth(self.attacker)
        res = self.client.post(self.url, {'email': 'target@test.local'},
                               format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(PasswordResetToken.objects.count(), 0)

    def test_ordinary_user_cannot_issue_a_reset_for_themselves(self):
        self.auth(self.attacker)
        res = self.client.post(self.url, {'email': 'attacker@test.local'},
                               format='json')
        self.assertEqual(res.status_code, 403)

    def test_ordinary_user_cannot_list_accounts(self):
        self.auth(self.attacker)
        self.assertEqual(self.client.get(self.users_url).status_code, 403)

    def test_unauthenticated_is_rejected(self):
        self.client.credentials()
        res = self.client.post(self.url, {'email': 'target@test.local'},
                               format='json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(PasswordResetToken.objects.count(), 0)

    def test_superuser_can_issue(self):
        self.auth(self.admin)
        res = self.client.post(self.url, {'email': 'target@test.local',
                                          'frontend_base': 'https://app.example'},
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['email'], 'target@test.local')
        self.assertIn('https://app.example/reset-password/', res.data['reset_url'])

    def test_superuser_can_list_accounts(self):
        self.auth(self.admin)
        res = self.client.get(self.users_url)
        self.assertEqual(res.status_code, 200)
        emails = {u['email'] for u in res.data}
        self.assertIn('target@test.local', emails)

    def test_no_password_is_ever_returned(self):
        """Passwords are hashes; nothing here may expose one."""
        self.auth(self.admin)
        res = self.client.post(self.url, {'email': 'target@test.local'},
                               format='json')
        blob = str(res.data).lower()
        for leak in ('password', 'pbkdf2', 'hash', 'oldpass'):
            if leak == 'password':
                # 'reset-password' appears in the URL, so check for a value field
                self.assertNotIn('"password"', blob)
                continue
            self.assertNotIn(leak, blob)

        listing = self.client.get(self.users_url)
        self.assertNotIn('pbkdf2', str(listing.data).lower())


class AdminResetBehaviourTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='a@test.local',
                                                   password='x', name='Admin')
        UserProfile.objects.create(user=self.admin, preferred_unit='lb')
        self.target = User.objects.create_user(email='t@test.local',
                                               password='oldpass', name='Target')
        UserProfile.objects.create(user=self.target, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('admin-reset-link')

    def issue(self, **extra):
        payload = {'email': 't@test.local', 'frontend_base': 'https://app.example'}
        payload.update(extra)
        return self.client.post(self.url, payload, format='json')

    def test_unknown_email_is_404(self):
        res = self.issue(email='nobody@test.local')
        self.assertEqual(res.status_code, 404)

    def test_missing_email_is_400(self):
        res = self.client.post(self.url, {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_email_is_case_insensitive(self):
        res = self.issue(email='T@TEST.LOCAL')
        self.assertEqual(res.status_code, 201, res.data)

    def test_default_expiry_is_24_hours(self):
        res = self.issue()
        self.assertEqual(res.data['expires_in_hours'], 24)
        token = PasswordResetToken.objects.get()
        delta = token.expires_at - timezone.now()
        self.assertGreater(delta.total_seconds(), 23 * 3600)
        self.assertLess(delta.total_seconds(), 25 * 3600)

    def test_expiry_is_clamped(self):
        self.assertEqual(self.issue(hours=500).data['expires_in_hours'], 72)
        PasswordResetToken.objects.all().delete()
        self.assertEqual(self.issue(hours=0).data['expires_in_hours'], 1)
        PasswordResetToken.objects.all().delete()
        self.assertEqual(self.issue(hours='abc').data['expires_in_hours'], 24)

    def test_issuing_again_supersedes_the_previous_link(self):
        """Only one live link per user, so an old one can't be reused later."""
        first = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        second = self.issue()
        self.assertEqual(second.data['superseded_links'], 1)

        old = PasswordResetToken.objects.get(token=first)
        self.assertFalse(old.is_valid, 'the earlier link must be dead')
        new = PasswordResetToken.objects.get(
            token=second.data['reset_url'].rsplit('/', 1)[-1])
        self.assertTrue(new.is_valid)

    def test_issued_link_works_with_the_existing_confirm_endpoint(self):
        """End to end: the link must drive the page that already exists."""
        token_str = self.issue().data['reset_url'].rsplit('/', 1)[-1]

        self.client.credentials()   # the user is not signed in
        res = self.client.post(reverse('password-reset-confirm'), {
            'token': token_str, 'password': 'a-brand-new-passw0rd!'},
            format='json')
        self.assertEqual(res.status_code, 200, res.data)

        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password('a-brand-new-passw0rd!'))
        self.assertFalse(self.target.check_password('oldpass'))

    def test_issued_link_is_single_use(self):
        token_str = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        self.client.credentials()
        confirm = reverse('password-reset-confirm')
        first = self.client.post(confirm, {'token': token_str,
                                           'password': 'first-passw0rd!'},
                                 format='json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post(confirm, {'token': token_str,
                                            'password': 'second-passw0rd!'},
                                  format='json')
        self.assertEqual(second.status_code, 400)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password('first-passw0rd!'))

    def test_expired_link_is_rejected(self):
        token_str = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        PasswordResetToken.objects.filter(token=token_str).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
            if hasattr(timezone, 'timedelta') else timezone.now())
        from datetime import timedelta
        PasswordResetToken.objects.filter(token=token_str).update(
            expires_at=timezone.now() - timedelta(minutes=1))

        self.client.credentials()
        res = self.client.post(reverse('password-reset-confirm'),
                               {'token': token_str, 'password': 'whatever-pw1!'},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password('oldpass'),
                        'an expired link must not change the password')

    def test_weak_password_is_rejected_by_the_confirm_endpoint(self):
        token_str = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        self.client.credentials()
        res = self.client.post(reverse('password-reset-confirm'),
                               {'token': token_str, 'password': '123'},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_frontend_base_falls_back_when_not_supplied(self):
        res = self.client.post(self.url, {'email': 't@test.local'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertIn('/reset-password/', res.data['reset_url'])

    def test_token_is_long_and_random(self):
        token_str = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        self.assertGreaterEqual(len(token_str), 32)
        other = self.issue().data['reset_url'].rsplit('/', 1)[-1]
        self.assertNotEqual(token_str, other)
