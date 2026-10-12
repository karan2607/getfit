"""
Tests for the feedback queue.

The superuser restriction is the security boundary for the automated
implementation loop: that loop reads this table and writes code from its
contents, so a non-superuser being able to write to it would turn the endpoint
into a prompt-injection surface against the repository. These tests assert the
restriction is enforced server-side, not merely by hiding the button.
"""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api.models import User, UserProfile, Feedback


class FeedbackAccessControlTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        UserProfile.objects.create(user=self.admin, preferred_unit='lb')
        self.plain = User.objects.create_user(email='plain@test.local', password='x')
        UserProfile.objects.create(user=self.plain, preferred_unit='lb')
        self.url = reverse('feedback')

    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    # -- the boundary -------------------------------------------------------

    def test_non_superuser_cannot_submit(self):
        self.auth(self.plain)
        res = self.client.post(self.url, {'body': 'please change this'},
                               format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Feedback.objects.count(), 0,
                         'nothing may reach the queue from a non-superuser')

    def test_non_superuser_cannot_list(self):
        Feedback.objects.create(user=self.admin, body='admin item')
        self.auth(self.plain)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)

    def test_non_superuser_cannot_read_update_or_delete_an_item(self):
        item = Feedback.objects.create(user=self.admin, body='admin item')
        detail = reverse('feedback-detail', args=[item.id])
        self.auth(self.plain)
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(
            self.client.patch(detail, {'status': 'done'}, format='json').status_code,
            403)
        self.assertEqual(self.client.delete(detail).status_code, 403)
        self.assertTrue(Feedback.objects.filter(pk=item.pk).exists())

    def test_unauthenticated_is_rejected(self):
        self.client.credentials()
        res = self.client.post(self.url, {'body': 'x'}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_superuser_can_submit(self):
        self.auth(self.admin)
        res = self.client.post(
            self.url, {'body': 'Make the warmup cards collapsible',
                       'page': '/workouts'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Feedback.objects.count(), 1)
        item = Feedback.objects.get()
        self.assertEqual(item.user, self.admin)
        self.assertEqual(item.status, 'new')
        self.assertEqual(item.page, '/workouts')

    # -- submitter cannot forge automation fields --------------------------

    def test_submitter_cannot_set_status_or_results(self):
        self.auth(self.admin)
        res = self.client.post(self.url, {
            'body': 'x',
            'status': 'done',
            'result_notes': 'already handled',
            'commit_sha': 'deadbeef',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        item = Feedback.objects.get()
        self.assertEqual(item.status, 'new', 'status is automation-owned')
        self.assertEqual(item.result_notes, '')
        self.assertEqual(item.commit_sha, '')

    # -- validation ---------------------------------------------------------

    def test_empty_body_is_rejected(self):
        self.auth(self.admin)
        for body in ('', '   ', None):
            res = self.client.post(self.url, {'body': body}, format='json')
            self.assertEqual(res.status_code, 400, msg='body=%r' % (body,))
        self.assertEqual(Feedback.objects.count(), 0)

    def test_overlong_body_is_rejected(self):
        self.auth(self.admin)
        res = self.client.post(self.url, {'body': 'a' * 5001}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_body_is_trimmed(self):
        self.auth(self.admin)
        self.client.post(self.url, {'body': '  spaced  '}, format='json')
        self.assertEqual(Feedback.objects.get().body, 'spaced')


class FeedbackQueueTests(APITestCase):
    """The read/update surface the automation loop uses."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin2@test.local', password='x')
        UserProfile.objects.create(user=self.admin, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('feedback')

    def test_list_is_newest_first(self):
        for body in ('first', 'second', 'third'):
            Feedback.objects.create(user=self.admin, body=body)
        res = self.client.get(self.url)
        self.assertEqual([r['body'] for r in res.data],
                         ['third', 'second', 'first'])

    def test_status_filter(self):
        Feedback.objects.create(user=self.admin, body='pending')
        Feedback.objects.create(user=self.admin, body='finished', status='done')
        res = self.client.get(self.url, {'status': 'new'})
        self.assertEqual([r['body'] for r in res.data], ['pending'])
        res = self.client.get(self.url, {'status': 'done'})
        self.assertEqual([r['body'] for r in res.data], ['finished'])

    def test_automation_can_report_progress(self):
        item = Feedback.objects.create(user=self.admin, body='do the thing')
        detail = reverse('feedback-detail', args=[item.id])

        res = self.client.patch(detail, {'status': 'in_progress'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        res = self.client.patch(detail, {
            'status': 'done',
            'result_notes': 'Collapsed the day cards.',
            'commit_sha': 'a' * 40,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        item.refresh_from_db()
        self.assertEqual(item.status, 'done')
        self.assertEqual(item.result_notes, 'Collapsed the day cards.')
        self.assertEqual(item.commit_sha, 'a' * 40)

    def test_automation_can_report_failure(self):
        item = Feedback.objects.create(user=self.admin, body='break it')
        res = self.client.patch(reverse('feedback-detail', args=[item.id]), {
            'status': 'failed',
            'result_notes': 'tsc --noEmit failed: TS1381',
        }, format='json')
        self.assertEqual(res.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, 'failed')
        self.assertIn('TS1381', item.result_notes)

    def test_invalid_status_is_rejected(self):
        item = Feedback.objects.create(user=self.admin, body='x')
        res = self.client.patch(reverse('feedback-detail', args=[item.id]),
                                {'status': 'shipped'}, format='json')
        self.assertEqual(res.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, 'new')

    def test_commit_sha_is_truncated_not_rejected(self):
        item = Feedback.objects.create(user=self.admin, body='x')
        self.client.patch(reverse('feedback-detail', args=[item.id]),
                          {'commit_sha': 'b' * 80}, format='json')
        item.refresh_from_db()
        self.assertEqual(len(item.commit_sha), 40)

    def test_delete_removes_the_item(self):
        item = Feedback.objects.create(user=self.admin, body='x')
        res = self.client.delete(reverse('feedback-detail', args=[item.id]))
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Feedback.objects.filter(pk=item.pk).exists())

    def test_missing_item_is_404(self):
        import uuid
        res = self.client.get(reverse('feedback-detail', args=[uuid.uuid4()]))
        self.assertEqual(res.status_code, 404)


class IsSuperuserExposureTests(APITestCase):
    """The client needs is_superuser to decide whether to show the button."""

    def test_me_reports_is_superuser(self):
        admin = User.objects.create_superuser(email='a3@test.local', password='x')
        UserProfile.objects.create(user=admin, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=admin)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.get(reverse('me'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['is_superuser'])

    def test_plain_user_reports_false(self):
        user = User.objects.create_user(email='p3@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        res = self.client.get(reverse('me'))
        self.assertFalse(res.data['is_superuser'])

    def test_is_superuser_cannot_be_escalated_via_patch(self):
        user = User.objects.create_user(email='p4@test.local', password='x')
        UserProfile.objects.create(user=user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.client.patch(reverse('me'), {'is_superuser': True}, format='json')
        user.refresh_from_db()
        self.assertFalse(user.is_superuser, 'privilege escalation via /auth/me/')
