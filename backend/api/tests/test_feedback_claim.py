"""
Claimable work: what the loop may take, decided by the server.

The loop used to poll `?status=new`. A run that died after setting
'in_progress' therefore orphaned its item -- nothing polled for that state,
and the bell only counts finished items, so the row was invisible to both.
One sat that way for 14 days.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.models import Feedback, User


class ClaimableQueueTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        self.auth(self.admin)
        self.url = reverse('feedback') + '?status=claimable'

    def auth(self, user):
        token = Token.objects.get_or_create(user=user)[0]
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def make(self, **kw):
        return Feedback.objects.create(user=self.admin, body='x', **kw)

    def claimable_ids(self):
        return {row['id'] for row in self.client.get(self.url).data}

    # -- what the loop gets -------------------------------------------

    def test_a_new_item_is_claimable(self):
        item = self.make(status='new')
        self.assertIn(str(item.id), self.claimable_ids())

    def test_a_fresh_claim_is_left_alone(self):
        """Or two runners would work the same item at once."""
        item = self.make(status='in_progress', claimed_at=timezone.now())
        self.assertNotIn(str(item.id), self.claimable_ids())

    def test_a_stale_claim_comes_back(self):
        item = self.make(
            status='in_progress',
            claimed_at=timezone.now() - Feedback.STALE_AFTER - timedelta(minutes=1),
        )
        self.assertIn(str(item.id), self.claimable_ids())

    def test_a_claim_from_before_this_existed_comes_back(self):
        """
        The 14-day row. Its status is 'in_progress' with no claimed_at,
        because nothing stamped one at the time. Null is treated as
        infinitely stale, which is what rescues it with no data migration.
        """
        item = self.make(status='in_progress', claimed_at=None)
        self.assertIn(str(item.id), self.claimable_ids())

    def test_finished_items_are_never_claimable(self):
        """The whole point of the state: the loop need not know what it did."""
        for st in Feedback.FINISHED:
            self.make(status=st)
        self.assertEqual(self.claimable_ids(), set())

    def test_an_item_past_the_attempt_cap_is_not_requeued(self):
        """
        Requeueing is only safe with a cap. An item that crashes the run
        every time would otherwise be taken, abandoned and taken again
        forever, blocking everything behind it.
        """
        item = self.make(status='new', attempts=Feedback.MAX_ATTEMPTS)
        self.assertNotIn(str(item.id), self.claimable_ids())

    def test_an_item_under_the_cap_is_still_requeued(self):
        item = self.make(status='new', attempts=Feedback.MAX_ATTEMPTS - 1)
        self.assertIn(str(item.id), self.claimable_ids())

    def test_the_plain_status_filter_still_works(self):
        """The runner changed; the queue admin did not."""
        self.make(status='new')
        self.make(status='done')
        rows = self.client.get(reverse('feedback') + '?status=done').data
        self.assertEqual([r['status'] for r in rows], ['done'])

    # -- taking an item ------------------------------------------------

    def test_taking_an_item_stamps_the_claim_and_counts_it(self):
        item = self.make(status='new')
        self.client.patch(reverse('feedback-detail', args=[item.id]),
                          {'status': 'in_progress'}, format='json')
        item.refresh_from_db()
        self.assertIsNotNone(item.claimed_at)
        self.assertEqual(item.attempts, 1)

    def test_a_second_patch_does_not_inflate_the_count(self):
        """The runner PATCHes more than once per item."""
        item = self.make(status='new')
        url = reverse('feedback-detail', args=[item.id])
        self.client.patch(url, {'status': 'in_progress'}, format='json')
        self.client.patch(url, {'result_notes': 'thinking'}, format='json')
        item.refresh_from_db()
        self.assertEqual(item.attempts, 1)

    def test_a_requeued_item_counts_a_second_attempt(self):
        item = self.make(status='new')
        url = reverse('feedback-detail', args=[item.id])
        self.client.patch(url, {'status': 'in_progress'}, format='json')
        # The run dies. The claim goes stale and another run takes it.
        Feedback.objects.filter(pk=item.pk).update(
            claimed_at=timezone.now() - Feedback.STALE_AFTER - timedelta(minutes=1))
        self.client.patch(url, {'status': 'new'}, format='json')
        self.client.patch(url, {'status': 'in_progress'}, format='json')
        item.refresh_from_db()
        self.assertEqual(item.attempts, 2)

    def test_three_abandoned_runs_stop_the_requeue(self):
        """End to end: the cap is what makes requeueing safe."""
        item = self.make(status='new')
        url = reverse('feedback-detail', args=[item.id])
        for _ in range(Feedback.MAX_ATTEMPTS):
            self.client.patch(url, {'status': 'in_progress'}, format='json')
            Feedback.objects.filter(pk=item.pk).update(claimed_at=None)
            self.client.patch(url, {'status': 'new'}, format='json')
        self.assertNotIn(str(item.id), self.claimable_ids())

    # -- the boundary --------------------------------------------------

    def test_a_non_superuser_cannot_read_the_claimable_queue(self):
        self.make(status='new')
        self.auth(User.objects.create_user(email='p@test.local', password='x'))
        self.assertIn(self.client.get(self.url).status_code, (403, 404))


class IsClaimablePropertyTests(APITestCase):
    """The rule itself, away from the endpoint."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')

    def make(self, **kw):
        return Feedback(user=self.admin, body='x', **kw)

    def test_new(self):
        self.assertTrue(self.make(status='new').is_claimable)

    def test_fresh_claim(self):
        self.assertFalse(
            self.make(status='in_progress', claimed_at=timezone.now()).is_claimable)

    def test_null_claim(self):
        self.assertTrue(self.make(status='in_progress').is_claimable)

    def test_done(self):
        self.assertFalse(self.make(status='done').is_claimable)

    def test_capped(self):
        self.assertFalse(
            self.make(status='new', attempts=Feedback.MAX_ATTEMPTS).is_claimable)
