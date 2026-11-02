"""
The chat SSE contract, pinned.

Why this file exists
--------------------
`chat_message_stream` is the single most-used AI surface in the app, and until
now NOTHING tested it: not the view, not its hand-rolled auth, not the SSE
framing, not the 20-message history window. The agent work replaces the model
call inside it, so the contract has to be written down first -- otherwise
"it still works" is an opinion.

These tests describe what the view does TODAY, deliberately including the
awkward parts, because the frontend depends on them:

  * `error` and `done` are mutually exclusive. On failure the generator emits
    an error frame and RETURNS, so no `done` ever follows. `lib/stream.ts`
    only stops on `done` or a thrown error, so emitting both, or neither,
    changes client behaviour.
  * a partial reply is still persisted when the stream dies, because the save
    is in a `finally`. The client currently discards it; the server does not.
  * the user's message is persisted BEFORE the model is called, so it survives
    a failure and is included in its own history window.

Anything here that later becomes undesirable should be changed deliberately,
with the test updated in the same commit.
"""

import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from api.gemini import GeminiError
from api.models import ChatMessage, ChatSession, User, UserProfile


def _frames(response):
    """The decoded JSON payload of each `data:` frame, in order."""
    body = b''.join(response.streaming_content).decode()
    out = []
    for block in body.split('\n\n'):
        block = block.strip()
        if block.startswith('data: '):
            out.append(json.loads(block[len('data: '):]))
    return out


class ChatStreamContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='chat@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.token = Token.objects.get_or_create(user=self.user)[0]
        self.session = ChatSession.objects.create(user=self.user)
        self.url = reverse('chat-message-stream', args=[self.session.id])

    def post(self, body=None, token=True, raw=None):
        kwargs = {'content_type': 'application/json'}
        if token:
            kwargs['HTTP_AUTHORIZATION'] = 'Token ' + self.token.key
        payload = raw if raw is not None else json.dumps(
            body if body is not None else {'content': 'hello'})
        return self.client.post(self.url, data=payload, **kwargs)

    def stream(self, chunks):
        return mock.patch('api.views.stream_gemini_chat',
                          return_value=iter(chunks))

    def failing_stream(self, chunks, message='AI stream failed (402)'):
        def fake(**_kwargs):
            for chunk in chunks:
                yield chunk
            raise GeminiError(message)
        return mock.patch('api.views.stream_gemini_chat', side_effect=fake)

    # -- framing ----------------------------------------------------------

    def test_the_response_is_an_unbuffered_sse_stream(self):
        with self.stream(['hi']):
            resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/event-stream')
        # Render buffers proxied responses by default, which would hold the
        # whole reply until completion and defeat streaming entirely.
        self.assertEqual(resp['X-Accel-Buffering'], 'no')
        self.assertEqual(resp['Cache-Control'], 'no-cache')

    def test_frames_are_data_lines_with_no_event_names(self):
        with self.stream(['a', 'b']):
            resp = self.post()
        body = b''.join(resp.streaming_content).decode()
        self.assertNotIn('event:', body)
        self.assertNotIn('retry:', body)
        self.assertTrue(body.endswith('\n\n'))
        for block in [b for b in body.split('\n\n') if b.strip()]:
            self.assertTrue(block.startswith('data: '), block)

    def test_chunks_then_done_with_a_message_id(self):
        with self.stream(['Hello ', 'world']):
            frames = _frames(self.post())
        self.assertEqual([f.get('chunk') for f in frames[:2]], ['Hello ', 'world'])
        self.assertIs(frames[-1]['done'], True)
        saved = ChatMessage.objects.get(session=self.session, role='assistant')
        self.assertEqual(frames[-1]['message_id'], str(saved.id))
        self.assertEqual(saved.content, 'Hello world')

    # -- the error path ---------------------------------------------------

    def test_no_done_frame_ever_follows_an_error(self):
        """
        THE contract the frontend leans on. `lib/stream.ts` returns on `done`;
        if an error were followed by `done` the client would treat a failure as
        a completed reply.
        """
        with self.failing_stream(['partial ']):
            frames = _frames(self.post())
        self.assertIn('error', frames[-1])
        self.assertEqual([f for f in frames if 'done' in f], [])

    def test_a_partial_reply_is_still_persisted_on_error(self):
        """
        The save sits in a `finally`, so the server keeps what arrived even
        though the client currently throws it away.
        """
        with self.failing_stream(['half a sentence']):
            _frames(self.post())
        saved = ChatMessage.objects.filter(session=self.session, role='assistant')
        self.assertEqual([m.content for m in saved], ['half a sentence'])

    def test_an_empty_reply_produces_neither_done_nor_a_message(self):
        with self.stream([]):
            frames = _frames(self.post())
        self.assertEqual(frames, [])
        self.assertFalse(
            ChatMessage.objects.filter(session=self.session, role='assistant').exists())

    # -- persistence and history -----------------------------------------

    def test_the_user_message_is_saved_before_the_model_runs(self):
        with self.failing_stream([]):
            self.post({'content': 'kept even though the model failed'})
        self.assertEqual(
            [m.content for m in ChatMessage.objects.filter(
                session=self.session, role='user')],
            ['kept even though the model failed'])

    def test_history_is_the_last_twenty_messages_with_assistant_as_model(self):
        for i in range(25):
            ChatMessage.objects.create(
                session=self.session,
                role='user' if i % 2 == 0 else 'assistant',
                content='m%d' % i)

        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return iter(['ok'])

        # The view's body is a generator, so nothing runs until the response
        # is consumed -- hence _frames() rather than a bare post().
        with mock.patch('api.views.stream_gemini_chat', side_effect=fake):
            _frames(self.post({'content': 'newest'}))

        history = captured['history']
        self.assertEqual(len(history), 20)
        # The user's own message is persisted first, so it is inside the window.
        self.assertEqual(history[-1]['parts'][0]['text'], 'newest')
        self.assertEqual(sorted({h['role'] for h in history}), ['model', 'user'])
        self.assertNotIn('assistant', {h['role'] for h in history})

    # -- rejections -------------------------------------------------------

    def test_requires_a_token(self):
        self.assertEqual(self.post(token=False).status_code, 401)

    def test_rejects_a_bad_token(self):
        resp = self.client.post(
            self.url, data=json.dumps({'content': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token not-a-real-token')
        self.assertEqual(resp.status_code, 401)

    def test_rejects_unparseable_json(self):
        self.assertEqual(self.post(raw='{not json').status_code, 400)

    def test_rejects_empty_content(self):
        self.assertEqual(self.post({'content': '   '}).status_code, 400)

    def test_another_users_session_is_not_found(self):
        other = User.objects.create_user(email='other@test.local', password='x')
        theirs = ChatSession.objects.create(user=other)
        resp = self.client.post(
            reverse('chat-message-stream', args=[theirs.id]),
            data=json.dumps({'content': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token ' + self.token.key)
        self.assertEqual(resp.status_code, 404)

    def test_get_is_not_allowed(self):
        resp = self.client.get(
            self.url, HTTP_AUTHORIZATION='Token ' + self.token.key)
        self.assertEqual(resp.status_code, 405)
