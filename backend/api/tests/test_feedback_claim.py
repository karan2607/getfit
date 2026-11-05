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


class UnknownStatusFilterTests(APITestCase):
    """
    An unknown filter must fail loudly, not look like an empty queue.

    It used to match nothing and return 200 with []. The runner was changed
    to ask for 'claimable' before the server knew the word -- the two deploy
    separately -- and it logged "queue empty" with real work waiting. The
    loop went blind and said nothing, which is the worst possible shape for
    this failure: silent, and indistinguishable from success.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        token = Token.objects.get_or_create(user=self.admin)[0]
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        Feedback.objects.create(user=self.admin, body='real work', status='new')

    def test_an_unknown_status_is_rejected(self):
        r = self.client.get(reverse('feedback') + '?status=claimabel')
        self.assertEqual(r.status_code, 400)
        self.assertIn('unknown status', r.data['detail'])

    def test_it_does_not_quietly_report_an_empty_queue(self):
        """The actual harm: work waiting, and the caller told there is none."""
        r = self.client.get(reverse('feedback') + '?status=nonsense')
        self.assertNotEqual(r.status_code, 200)

    def test_every_real_status_is_still_accepted(self):
        for st, _label in Feedback.STATUS_CHOICES:
            r = self.client.get(reverse('feedback') + f'?status={st}')
            self.assertEqual(r.status_code, 200, st)

    def test_claimable_is_accepted(self):
        r = self.client.get(reverse('feedback') + '?status=claimable')
        self.assertEqual(r.status_code, 200)

    def test_no_filter_still_lists_everything(self):
        r = self.client.get(reverse('feedback'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)


class AttemptResetTests(APITestCase):
    """
    The runner may give back an attempt it wasted on its own bug.

    Attempts are counted when an item is TAKEN, which is right: a run that
    dies has still used one. But it means a bug in the runner spends the
    item's budget. Three items were burned on an empty prompt the runner
    built itself, and without this nothing could give them back -- a runner
    bug would permanently retire whatever was in the queue when it shipped.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        token = Token.objects.get_or_create(user=self.admin)[0]
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.item = Feedback.objects.create(
            user=self.admin, body='x', status='in_progress', attempts=2)
        self.url = reverse('feedback-detail', args=[self.item.id])

    def test_attempts_can_be_reset(self):
        self.client.patch(self.url, {'status': 'new', 'attempts': 0},
                          format='json')
        self.item.refresh_from_db()
        self.assertEqual(self.item.attempts, 0)

    def test_a_reset_item_is_claimable_again(self):
        """The point of the reset: it goes back in the queue."""
        self.item.attempts = Feedback.MAX_ATTEMPTS
        self.item.save()
        self.client.patch(self.url, {'status': 'new', 'attempts': 0},
                          format='json')
        rows = self.client.get(reverse('feedback') + '?status=claimable').data
        self.assertIn(str(self.item.id), {r['id'] for r in rows})

    def test_omitting_attempts_leaves_the_count_alone(self):
        """A normal report-back must not quietly clear the budget."""
        self.client.patch(self.url, {'status': 'failed',
                                     'result_notes': 'did not work'},
                          format='json')
        self.item.refresh_from_db()
        self.assertEqual(self.item.attempts, 2)

    def test_a_non_integer_is_refused(self):
        r = self.client.patch(self.url, {'attempts': 'lots'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.attempts, 2)

    def test_a_negative_count_is_clamped(self):
        self.client.patch(self.url, {'attempts': -5}, format='json')
        self.item.refresh_from_db()
        self.assertEqual(self.item.attempts, 0)

    def test_a_non_superuser_cannot_reset_attempts(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects
                                .get_or_create(user=User.objects.create_user(
                                    email='p@test.local', password='x'))[0].key)
        self.client.patch(self.url, {'attempts': 0}, format='json')
        self.item.refresh_from_db()
        self.assertEqual(self.item.attempts, 2)
