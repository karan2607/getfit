"""
Conversations that belong to something, and survive a reload.

Reported as: a chat started inside a workout is gone the moment the page
reloads, so the whole message has to be retyped.

The drawer created a brand new `embedded` session on every open and held
the id in React state alone. Nothing tied a conversation to the workout,
day, plan or meal it was about, so there was no way to look one up again --
and every abandoned session stayed in this table, unreachable.

Identity is the most specific thing the caller has: a workout session gets
its own thread, and a day inside it gets another, because they are
conversations about different things.
"""

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.models import ChatMessage, ChatSession, User, UserProfile


class ContextThreadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='threads@test.local',
                                             password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('chat-sessions')

    def start(self, **context):
        body = {'source': 'embedded'}
        body.update(context)
        response = self.client.post(self.url, body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def threads_for(self, context_type, context_id):
        response = self.client.get(
            self.url, {'context_type': context_type, 'context_id': context_id})
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_a_thread_can_be_found_again_by_its_context(self):
        started = self.start(context_type='workout_session',
                             context_id='w1')
        found = self.threads_for('workout_session', 'w1')
        self.assertEqual([t['id'] for t in found], [started['id']])

    def test_a_day_inside_a_workout_is_a_different_thread(self):
        session_thread = self.start(context_type='workout_session',
                                    context_id='w1')
        day_thread = self.start(context_type='plan_day', context_id='d1')

        self.assertNotEqual(session_thread['id'], day_thread['id'])
        self.assertEqual(
            [t['id'] for t in self.threads_for('workout_session', 'w1')],
            [session_thread['id']])
        self.assertEqual(
            [t['id'] for t in self.threads_for('plan_day', 'd1')],
            [day_thread['id']])

    def test_two_plans_do_not_share_a_thread(self):
        first = self.start(context_type='plan', context_id='p1')
        second = self.start(context_type='plan', context_id='p2')
        self.assertEqual([t['id'] for t in self.threads_for('plan', 'p1')],
                         [first['id']])
        self.assertEqual([t['id'] for t in self.threads_for('plan', 'p2')],
                         [second['id']])

    def test_several_threads_on_one_context_come_back_newest_first(self):
        """'New chat' is deliberate, so a context can hold more than one."""
        older = self.start(context_type='plan', context_id='p1')
        newer = self.start(context_type='plan', context_id='p1')
        found = self.threads_for('plan', 'p1')
        self.assertEqual([t['id'] for t in found],
                         [newer['id'], older['id']])

    def test_the_picker_can_tell_the_threads_apart(self):
        thread = self.start(context_type='plan', context_id='p1')
        ChatMessage.objects.create(
            session=ChatSession.objects.get(pk=thread['id']),
            role='user', content='swap my squats')
        found = self.threads_for('plan', 'p1')
        self.assertEqual(found[0]['message_count'], 1)

    def test_context_threads_stay_out_of_the_main_chat_list(self):
        """
        One row per day, meal and workout ever discussed would bury the
        handful of conversations the AI page is actually for.
        """
        self.start(context_type='plan', context_id='p1')
        main = self.client.post(self.url, {}, format='json').data
        listed = self.client.get(self.url).data
        self.assertEqual([t['id'] for t in listed], [main['id']])

    def test_another_users_thread_is_not_offered(self):
        self.start(context_type='plan', context_id='p1')
        intruder = User.objects.create_user(email='nope3@test.local',
                                            password='x')
        token, _ = Token.objects.get_or_create(user=intruder)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(self.threads_for('plan', 'p1'), [])

    def test_a_context_with_no_thread_yet_is_simply_empty(self):
        self.assertEqual(self.threads_for('plan', 'never-chatted'), [])

    def test_an_unknown_context_type_is_refused(self):
        response = self.client.post(
            self.url,
            {'source': 'embedded', 'context_type': 'something_invented',
             'context_id': 'x'},
            format='json')
        self.assertEqual(response.status_code, 400)

    def test_half_a_context_is_refused(self):
        """
        A type with no id would key every plan's thread to the same row, so
        opening any plan would show another plan's conversation.
        """
        response = self.client.post(
            self.url, {'source': 'embedded', 'context_type': 'plan'},
            format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_chat_with_no_context_still_works(self):
        """The AI page, and the unsaved plan preview that has no id to key on."""
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['context_type'], '')
