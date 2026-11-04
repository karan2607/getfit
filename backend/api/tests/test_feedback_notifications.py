"""
The bell: finished feedback the submitter has not been shown yet.

Before this, the queue recorded `status`, `result_notes` and `commit_sha`
and nothing ever told the submitter an item had shipped -- you had to go
looking in the queue to find out.
"""
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.models import Feedback, User


class FeedbackNotificationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        self.other_admin = User.objects.create_superuser(
            email='admin2@test.local', password='x')
        self.plain = User.objects.create_user(email='plain@test.local', password='x')
        self.url = reverse('feedback-notifications')

    def auth(self, user):
        token = Token.objects.get_or_create(user=user)[0]
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def make(self, user=None, status='done', seen=False):
        return Feedback.objects.create(
            user=user or self.admin, body='make the bell work', status=status,
            seen_at=timezone.now() if seen else None)

    # -- what counts as news -------------------------------------------

    def test_a_finished_item_is_reported(self):
        self.make(status='done')
        self.auth(self.admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['unseen'], 1)

    def test_failed_and_skipped_count_too(self):
        """An item that failed is news. Silence would read as still working."""
        self.make(status='failed')
        self.make(status='skipped')
        self.auth(self.admin)
        self.assertEqual(self.client.get(self.url).data['unseen'], 2)

    def test_queued_and_in_progress_are_not_news(self):
        """Otherwise the bell is lit from the moment anything is submitted."""
        self.make(status='new')
        self.make(status='in_progress')
        self.auth(self.admin)
        self.assertEqual(self.client.get(self.url).data['unseen'], 0)

    def test_an_item_already_seen_is_not_reported_again(self):
        self.make(status='done', seen=True)
        self.auth(self.admin)
        self.assertEqual(self.client.get(self.url).data['unseen'], 0)

    def test_another_operators_item_is_not_your_notification(self):
        """
        The queue is shared on purpose -- it is one work list -- but being
        told someone else's item shipped is not news you can act on.
        """
        self.make(user=self.other_admin, status='done')
        self.auth(self.admin)
        self.assertEqual(self.client.get(self.url).data['unseen'], 0)

    # -- acknowledging --------------------------------------------------

    def test_marking_seen_clears_the_badge(self):
        self.make(status='done')
        self.auth(self.admin)
        self.assertEqual(self.client.post(self.url, {}, format='json').data['marked'], 1)
        self.assertEqual(self.client.get(self.url).data['unseen'], 0)

    def test_marking_seen_can_name_specific_items(self):
        a, b = self.make(status='done'), self.make(status='done')
        self.auth(self.admin)
        self.client.post(self.url, {'ids': [str(a.id)]}, format='json')
        r = self.client.get(self.url)
        self.assertEqual(r.data['unseen'], 1)
        self.assertEqual(r.data['items'][0]['id'], str(b.id))

    def test_marking_seen_cannot_touch_another_operators_items(self):
        theirs = self.make(user=self.other_admin, status='done')
        self.auth(self.admin)
        self.client.post(self.url, {'ids': [str(theirs.id)]}, format='json')
        theirs.refresh_from_db()
        self.assertIsNone(theirs.seen_at)

    # -- the boundary ---------------------------------------------------

    def test_a_non_superuser_is_refused(self):
        """
        Same boundary as the queue itself. This table feeds a loop that
        writes code, so nothing about it is readable by ordinary users.
        """
        self.make(status='done')
        self.auth(self.plain)
        self.assertIn(self.client.get(self.url).status_code, (403, 404))

    def test_an_anonymous_request_is_refused(self):
        self.client.credentials()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_a_non_superuser_cannot_mark_anything_seen(self):
        item = self.make(status='done')
        self.auth(self.plain)
        self.client.post(self.url, {}, format='json')
        item.refresh_from_db()
        self.assertIsNone(item.seen_at)

    def test_the_route_is_not_swallowed_by_the_detail_route(self):
        """'notifications' must not be parsed as a feedback id."""
        self.assertEqual(self.url, '/api/feedback/notifications/')
