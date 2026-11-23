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
import os
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from api import agent_router
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
    """
    The SSE contract, pinned on the Gemini engine.

    The engine is pinned explicitly rather than inherited from the
    AGENT_CHAT_ENABLED default. These tests used to pass because the agent
    was off by default, so flipping that default silently moved them onto an
    engine they do not mock -- a test that reads as a contract check but is
    really a check on a deployment flag. `test_the_framing_is_identical_on
    _the_agent_engine` covers the other side.
    """

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '0'})
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_the_framing_is_identical_on_the_agent_engine(self):
        """
        Swapping the engine must not change a single frame.

        The agent was introduced on the promise that it replaced only the
        model call inside the view, leaving the SSE contract untouched. That
        promise is only worth something if something checks it on the agent
        side too.
        """
        with mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '1'}), \
                mock.patch('api.views.agent_router.classify',
                           return_value=agent_router.WORKOUT), \
                mock.patch('api.views.agent_loop.run',
                           return_value=iter(['Hello ', 'world'])):
            resp = self.post()
            body = b''.join(resp.streaming_content).decode()
        frames = [json.loads(b[len('data: '):])
                  for b in body.split('\n\n') if b.strip()]
        self.assertNotIn('event:', body)
        self.assertEqual([f.get('chunk') for f in frames[:2]],
                         ['Hello ', 'world'])
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

    def test_an_empty_reply_still_ends_the_turn(self):
        """
        This used to assert `frames == []` -- the silence was the contract.

        It is not a defensible one. A final round that produces no text (all
        tool calls, or an empty text block) sent the client NOTHING: no
        `done`, no `error`, just a closed socket. The client cannot tell that
        from a dead connection, so it waited out its full 90s idle timeout
        and then blamed the network. Worse, a draft proposed during that turn
        was already committed server-side and never offered.

        So an empty reply is now a failed turn, said plainly and at once.
        Still no assistant message: there was nothing to save.
        """
        with self.stream([]):
            frames = _frames(self.post())
        self.assertEqual(len(frames), 1)
        self.assertIn('error', frames[0])
        self.assertNotIn('done', frames[0])
        self.assertNotIn('message_id', frames[0])
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


class TurnAlwaysTerminatesTests(TestCase):
    """
    A turn ends with exactly one frame, whatever goes wrong.

    Reported as the chat "not being foolproof", and before this every one of
    these cases ended in silence on the wire. The client cannot tell silence
    from a dead socket, so it waited out its 90s idle timeout and then told
    the user the network had failed -- for a bug, a database blip, or a
    perfectly ordinary turn that happened to produce no text.

    The expensive thing to lose is a draft. `propose_plan_change` commits its
    PlanChange row during the turn, so a turn that dies quietly leaves the
    server holding a change the user is never offered: the "no update plan
    button" report, twice over.
    """

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '0'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(email='term@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.token = Token.objects.get_or_create(user=self.user)[0]
        self.session = ChatSession.objects.create(user=self.user)
        self.url = reverse('chat-message-stream', args=[self.session.id])

    def post(self, body=None):
        return self.client.post(
            self.url,
            data=json.dumps(body if body is not None else {'content': 'hello'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token ' + self.token.key)

    def crashing_stream(self, chunks, exc=None):
        """A failure that is NOT a model error -- a bug, in other words."""
        def fake(**_kwargs):
            for chunk in chunks:
                yield chunk
            raise exc or RuntimeError('something nobody anticipated')
        return mock.patch('api.views.stream_gemini_chat', side_effect=fake)

    def test_a_bug_mid_stream_still_ends_the_turn(self):
        with self.crashing_stream(['half a thought ']):
            frames = _frames(self.post())
        self.assertIn('error', frames[-1])
        self.assertEqual([f for f in frames if 'done' in f], [])

    def test_a_bug_is_not_dressed_up_as_a_model_failure(self):
        """
        The user is told something broke on our side, not handed the
        exception text -- a RuntimeError's message is for the logs.
        """
        with self.crashing_stream([], exc=RuntimeError('KeyError: user_id')):
            frames = _frames(self.post())
        self.assertNotIn('KeyError', frames[-1]['error'])
        self.assertIn('our side', frames[-1]['error'])

    def test_a_crash_is_logged_so_sentry_sees_it(self):
        with self.crashing_stream([]):
            with self.assertLogs('api.views', level='ERROR'):
                _frames(self.post())

    def test_a_model_failure_is_logged_too(self):
        """
        This path told the user and NOBODY else: no logging at all, so a
        model outage reached Sentry only as a user complaint.
        """
        def fake(**_kwargs):
            raise GeminiError('AI stream failed (402)')
            yield  # pragma: no cover - generator marker
        with mock.patch('api.views.stream_gemini_chat', side_effect=fake):
            with self.assertLogs('api.views', level='ERROR'):
                _frames(self.post())

    def test_a_reply_that_cannot_be_saved_still_ends_the_turn(self):
        """
        The save used to live in `finally`, so an exception here replaced the
        frame that was about to go out -- losing the reply AND the draft, and
        hanging the client. A dropped connection after a 60s model call is
        ordinary, not exotic.
        """
        real_create = ChatMessage.objects.create

        def only_the_reply_fails(**kwargs):
            # The user's message is saved before the model runs and must
            # still succeed; it is the assistant row this is about.
            if kwargs.get('role') == 'assistant':
                raise RuntimeError('connection already closed')
            return real_create(**kwargs)

        with mock.patch('api.views.stream_gemini_chat',
                        return_value=iter(['a complete answer'])), \
             mock.patch('api.views.ChatMessage.objects.create',
                        side_effect=only_the_reply_fails):
            frames = _frames(self.post())
        self.assertIn('error', frames[-1])
        self.assertIn('could not be saved', frames[-1]['error'])

    def test_an_empty_turn_ends_with_an_error_not_silence(self):
        with mock.patch('api.views.stream_gemini_chat', return_value=iter([])):
            frames = _frames(self.post())
        self.assertEqual(len(frames), 1)
        self.assertIn('error', frames[0])

    def on_the_agent_path(self, chunks):
        """
        The ids are only looked up on the agent path, so a test that leaves
        the Gemini engine pinned never reaches the bug -- the first version
        of these two passed against the unfixed code for exactly that
        reason.
        """
        def fake(**_kwargs):
            for chunk in chunks:
                yield chunk
        return (
            mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '1'}),
            mock.patch.object(agent_router, 'classify',
                              return_value=agent_router.WORKOUT),
            mock.patch('api.agent_loop.run', side_effect=fake),
        )

    def test_a_malformed_plan_id_answers_instead_of_500ing(self):
        """
        A non-UUID went straight into `filter(pk=...)`, which raises rather
        than matching nothing -- and it raised BEFORE the response started,
        so the client got an HTML 500 where it expected text/event-stream,
        with the user's message already saved and no reply beside it.
        """
        env, classify, run = self.on_the_agent_path(['answered anyway'])
        with env, classify, run:
            response = self.post({'content': 'hi', 'plan_id': 'not-a-uuid'})
            frames = _frames(response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(frames[-1].get('done'))

    def test_every_context_id_tolerates_rubbish(self):
        env, classify, run = self.on_the_agent_path(['ok'])
        with env, classify, run:
            frames = _frames(self.post({
                'content': 'hi', 'plan_id': '../etc', 'day_id': '7',
                'diet_plan_id': '{}', 'meal_id': 'null',
            }))
        self.assertTrue(frames[-1].get('done'))


class TerminalFrameCarriesTheDraftTests(TestCase):
    """
    A draft reaches the client on whichever frame ends the turn.

    It used to ride on `done` alone, so every failure path dropped it while
    the PlanChange row stayed committed on the server -- a change the user
    was never shown and could not confirm.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='draft@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        self.token = Token.objects.get_or_create(user=self.user)[0]
        self.session = ChatSession.objects.create(user=self.user)
        self.url = reverse('chat-message-stream', args=[self.session.id])
        self.draft = {'change_id': 'c1', 'plan_id': 'p1', 'draft': [],
                      'needs_confirmation': True, 'applied': False}

    def post(self):
        return self.client.post(
            self.url, data=json.dumps({'content': 'move my pull day'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token ' + self.token.key)

    def run_with(self, chunks, proposal, raise_after=False):
        """Drive the agent engine, recording a proposal as the loop would."""
        def fake(*, run_state, **_kwargs):
            for chunk in chunks:
                yield chunk
            run_state.proposed_change = proposal
            if raise_after:
                raise RuntimeError('died after proposing')
        return mock.patch('api.agent_loop.run', side_effect=fake)

    def test_the_done_frame_carries_it(self):
        with mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '1'}), \
             mock.patch.object(agent_router, 'classify',
                               return_value=agent_router.WORKOUT), \
             self.run_with(['here is the change'], self.draft):
            frames = _frames(self.post())
        self.assertEqual(frames[-1]['proposed_change'], self.draft)
        self.assertTrue(frames[-1]['needs_confirmation'])

    def test_the_error_frame_carries_it_too(self):
        with mock.patch.dict(os.environ, {'AGENT_CHAT_ENABLED': '1'}), \
             mock.patch.object(agent_router, 'classify',
                               return_value=agent_router.WORKOUT), \
             self.run_with(['drafted it, then '], self.draft, raise_after=True):
            frames = _frames(self.post())
        self.assertIn('error', frames[-1])
        self.assertEqual(frames[-1]['proposed_change'], self.draft)
        self.assertTrue(frames[-1]['needs_confirmation'])
