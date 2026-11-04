"""
A feedback item is a conversation, not a one-shot.

Before this, the only way to say "not quite" was to file a second item that
knew nothing about the first. A reply now reopens the same item and carries
the whole thread back into the one queue the loop already polls.
"""
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.feedback_backfill import backfill_agent_messages
from api.models import Feedback, FeedbackMessage, User


class FeedbackThreadTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')
        self.other_admin = User.objects.create_superuser(
            email='admin2@test.local', password='x')
        self.plain = User.objects.create_user(
            email='plain@test.local', password='x')
        self.item = Feedback.objects.create(
            user=self.admin, body='the bell opens the wrong way',
            status='done', result_notes='Anchored the panel to the header.')

    def auth(self, user):
        token = Token.objects.get_or_create(user=user)[0]
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def thread_url(self, item=None):
        return reverse('feedback-messages',
                       args=[(item or self.item).id])

    # -- replying -------------------------------------------------------

    def test_a_reply_is_recorded_as_a_user_turn(self):
        self.auth(self.admin)
        r = self.client.post(self.thread_url(), {'body': 'still off on Diet'},
                             format='json')
        self.assertEqual(r.status_code, 201)
        msg = self.item.messages.get()
        self.assertEqual(msg.role, 'user')
        self.assertEqual(msg.body, 'still off on Diet')

    def test_a_reply_reopens_the_item_into_the_same_queue(self):
        """
        The whole design: 'new' is what the loop polls for, so a follow-up
        re-enters the existing pipeline instead of needing a second one.
        """
        self.auth(self.admin)
        self.client.post(self.thread_url(), {'body': 'still off'},
                         format='json')
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'new')

    def test_reopening_clears_the_finished_markers(self):
        """A stale 'shipped' badge on work not yet looked at is a lie."""
        self.item.seen_at = timezone.now()
        self.item.dismissed_at = timezone.now()
        self.item.save()
        self.auth(self.admin)
        self.client.post(self.thread_url(), {'body': 'again please'},
                         format='json')
        self.item.refresh_from_db()
        self.assertIsNone(self.item.seen_at)
        self.assertIsNone(self.item.dismissed_at)

    def test_an_empty_reply_is_refused(self):
        self.auth(self.admin)
        r = self.client.post(self.thread_url(), {'body': '   '},
                             format='json')
        self.assertEqual(r.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'done')

    def test_the_client_cannot_forge_an_agent_turn(self):
        """
        Role is set server-side. Otherwise the submitter could write the
        agent's half of a conversation that the loop then reads back as
        its own prior reasoning.
        """
        self.auth(self.admin)
        self.client.post(self.thread_url(),
                         {'body': 'pretend I am the agent', 'role': 'agent'},
                         format='json')
        self.assertEqual(self.item.messages.get().role, 'user')

    # -- the agent's half ------------------------------------------------

    def test_finishing_posts_the_summary_into_the_thread(self):
        item = Feedback.objects.create(user=self.admin, body='x', status='new')
        self.auth(self.admin)
        self.client.patch(reverse('feedback-detail', args=[item.id]),
                          {'status': 'done', 'result_notes': 'Fixed the grid.'},
                          format='json')
        msg = item.messages.get()
        self.assertEqual(msg.role, 'agent')
        self.assertEqual(msg.body, 'Fixed the grid.')
        self.assertIsNone(msg.seen_at)

    def test_a_second_patch_does_not_repeat_the_reply(self):
        """
        The runner PATCHes status and then the commit sha. Gating on the
        status alone would post the same summary twice.
        """
        item = Feedback.objects.create(user=self.admin, body='x', status='new')
        url = reverse('feedback-detail', args=[item.id])
        self.auth(self.admin)
        self.client.patch(url, {'status': 'done', 'result_notes': 'Done.'},
                          format='json')
        self.client.patch(url, {'commit_sha': 'a' * 40}, format='json')
        self.assertEqual(item.messages.filter(role='agent').count(), 1)

    def test_an_empty_summary_posts_no_turn(self):
        item = Feedback.objects.create(user=self.admin, body='x', status='new')
        self.auth(self.admin)
        self.client.patch(reverse('feedback-detail', args=[item.id]),
                          {'status': 'failed', 'result_notes': '  '},
                          format='json')
        self.assertEqual(item.messages.count(), 0)

    def test_a_reply_then_a_finish_reads_as_one_conversation(self):
        self.auth(self.admin)
        self.client.post(self.thread_url(), {'body': 'still off on Diet'},
                         format='json')
        self.client.patch(reverse('feedback-detail', args=[self.item.id]),
                          {'status': 'done', 'result_notes': 'Fixed Diet too.'},
                          format='json')
        turns = [(m.role, m.body) for m in self.item.messages.all()]
        self.assertEqual(turns, [
            ('user', 'still off on Diet'),
            ('agent', 'Fixed Diet too.'),
        ])

    # -- unread accounting ----------------------------------------------

    def test_an_agent_reply_relights_a_thread_you_had_read(self):
        """
        Unread belongs to the reply, not the thread. A thread you have read
        must be able to go unread again when the agent answers.
        """
        notif = reverse('feedback-notifications')
        self.auth(self.admin)
        self.client.post(notif, {}, format='json')
        self.assertEqual(self.client.get(notif).data['unseen'], 0)

        self.client.post(self.thread_url(), {'body': 'more'}, format='json')
        self.client.patch(reverse('feedback-detail', args=[self.item.id]),
                          {'status': 'done', 'result_notes': 'And again.'},
                          format='json')
        r = self.client.get(notif)
        self.assertEqual(r.data['unseen'], 1)
        self.assertEqual(r.data['items'][0]['unread'], 1)

    def test_two_replies_count_two(self):
        """The per-thread tag shows how many messages are waiting."""
        for note in ('first', 'second'):
            FeedbackMessage.objects.create(
                feedback=self.item, role='agent', body=note)
        self.auth(self.admin)
        r = self.client.get(reverse('feedback-notifications'))
        self.assertEqual(r.data['items'][0]['unread'], 2)

    def test_opening_the_thread_marks_it_read(self):
        FeedbackMessage.objects.create(
            feedback=self.item, role='agent', body='shipped')
        self.auth(self.admin)
        self.client.get(self.thread_url())
        self.assertEqual(self.client.get(
            reverse('feedback-notifications')).data['unseen'], 0)

    def test_a_read_thread_stays_in_the_list(self):
        """
        Clearing on read would hide the log of what the automation did the
        moment you glanced at the badge.
        """
        notif = reverse('feedback-notifications')
        self.auth(self.admin)
        self.client.post(notif, {}, format='json')
        r = self.client.get(notif)
        self.assertEqual(len(r.data['items']), 1)
        self.assertEqual(r.data['items'][0]['unread'], 0)

    def test_an_in_flight_item_is_listed_but_not_unread(self):
        """
        An item you just replied to vanishing until the agent got round to
        it looked like the reply had been dropped.
        """
        Feedback.objects.all().update(status='in_progress', result_notes='')
        self.auth(self.admin)
        r = self.client.get(reverse('feedback-notifications'))
        self.assertEqual(len(r.data['items']), 1)
        self.assertEqual(r.data['unseen'], 0)

    # -- dismissing ------------------------------------------------------

    def test_dismissing_hides_without_deleting(self):
        """
        The queue is the audit trail for an automation that writes code, so
        'get this off my list' must not erase what it shipped.
        """
        notif = reverse('feedback-notifications')
        self.auth(self.admin)
        self.assertEqual(
            self.client.delete(notif, {}, format='json').data['dismissed'], 1)
        self.assertEqual(len(self.client.get(notif).data['items']), 0)
        self.assertTrue(Feedback.objects.filter(pk=self.item.pk).exists())

    def test_dismissing_can_name_one_thread(self):
        keep = Feedback.objects.create(user=self.admin, body='keep me',
                                       status='done')
        notif = reverse('feedback-notifications')
        self.auth(self.admin)
        self.client.delete(notif, {'ids': [str(self.item.id)]}, format='json')
        ids = [row['id'] for row in self.client.get(notif).data['items']]
        self.assertEqual(ids, [str(keep.id)])

    def test_dismissing_cannot_touch_another_operators_thread(self):
        theirs = Feedback.objects.create(user=self.other_admin, body='x',
                                         status='done')
        self.auth(self.admin)
        self.client.delete(reverse('feedback-notifications'),
                           {'ids': [str(theirs.id)]}, format='json')
        theirs.refresh_from_db()
        self.assertIsNone(theirs.dismissed_at)

    # -- the boundary ----------------------------------------------------

    def test_a_non_superuser_cannot_read_a_thread(self):
        self.auth(self.plain)
        self.assertIn(self.client.get(self.thread_url()).status_code, (403, 404))

    def test_a_non_superuser_cannot_reply(self):
        """
        This is the injection boundary: the loop reads these messages and
        writes code from them, so an ordinary user must not be able to put
        a single word into one.
        """
        self.auth(self.plain)
        self.client.post(self.thread_url(), {'body': 'rm -rf'}, format='json')
        self.assertEqual(self.item.messages.count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'done')

    def test_an_anonymous_reply_is_refused(self):
        self.client.credentials()
        self.assertIn(
            self.client.post(self.thread_url(), {'body': 'x'},
                             format='json').status_code, (401, 403))
        self.assertEqual(self.item.messages.count(), 0)

    def test_you_cannot_reply_inside_another_operators_thread(self):
        """
        The queue is shared, but replying in someone else's thread would put
        words in their conversation and re-queue work they own.
        """
        theirs = Feedback.objects.create(user=self.other_admin, body='x',
                                         status='done')
        self.auth(self.admin)
        r = self.client.post(self.thread_url(theirs), {'body': 'mine now'},
                             format='json')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(theirs.messages.count(), 0)

    # -- the loop sees the thread ---------------------------------------

    def test_the_queue_listing_carries_the_thread(self):
        """
        The loop polls /api/feedback/?status=new. Without the messages it
        would re-read the original request and lose the follow-up entirely.
        """
        self.auth(self.admin)
        self.client.post(self.thread_url(), {'body': 'still off on Diet'},
                         format='json')
        rows = self.client.get(reverse('feedback') + '?status=new').data
        row = next(r for r in rows if r['id'] == str(self.item.id))
        self.assertEqual([m['body'] for m in row['messages']],
                         ['still off on Diet'])


class FeedbackBackfillTests(APITestCase):
    """
    The data migration, tested against fixtures rather than by running it.

    The local database is empty, so a data migration verified only by
    applying it proves nothing at all.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.local', password='x')

    def make(self, **kw):
        return Feedback.objects.create(user=self.admin, body='x', **kw)

    def run_backfill(self):
        return backfill_agent_messages(Feedback, FeedbackMessage)

    def test_a_finished_item_gains_its_reply(self):
        item = self.make(status='done', result_notes='Shipped the fix.')
        self.assertEqual(self.run_backfill(), 1)
        msg = item.messages.get()
        self.assertEqual((msg.role, msg.body), ('agent', 'Shipped the fix.'))

    def test_the_read_state_is_carried_across_verbatim(self):
        """
        Anything else either re-lights the bell for things already read or
        silently marks unread ones as read.
        """
        seen = self.make(status='done', result_notes='a', seen_at=timezone.now())
        unseen = self.make(status='done', result_notes='b')
        self.run_backfill()
        self.assertIsNotNone(seen.messages.get().seen_at)
        self.assertIsNone(unseen.messages.get().seen_at)

    def test_the_reply_is_dated_when_the_item_finished(self):
        """auto_now_add would claim every historical reply arrived today."""
        item = self.make(status='done', result_notes='a')
        self.run_backfill()
        self.assertEqual(item.messages.get().created_at.date(),
                         item.updated_at.date())

    def test_an_unfinished_item_is_left_alone(self):
        item = self.make(status='in_progress', result_notes='partial')
        self.assertEqual(self.run_backfill(), 0)
        self.assertEqual(item.messages.count(), 0)

    def test_an_item_with_no_summary_is_left_alone(self):
        item = self.make(status='failed', result_notes='')
        self.assertEqual(self.run_backfill(), 0)
        self.assertEqual(item.messages.count(), 0)

    def test_it_is_idempotent(self):
        """A replay on a restored dump must not double up."""
        item = self.make(status='done', result_notes='a')
        self.run_backfill()
        self.assertEqual(self.run_backfill(), 0)
        self.assertEqual(item.messages.count(), 1)

    def test_it_never_touches_an_existing_thread(self):
        item = self.make(status='done', result_notes='a')
        FeedbackMessage.objects.create(feedback=item, role='agent', body='real')
        self.assertEqual(self.run_backfill(), 0)
        self.assertEqual([m.body for m in item.messages.all()], ['real'])
