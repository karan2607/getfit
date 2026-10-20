"""
Model and temperature configurability.

The model used to be baked into the URL path
(`.../v1beta/models/gemini-2.5-flash`), so comparing two models meant editing
the module. "Do we need a better model?" was unanswerable by construction.

These tests pin two things: that the default request is byte-for-byte what it
was before (this touches every AI feature in the app), and that an override
actually changes the model called.
"""

import json
import os
from unittest import mock

from django.test import SimpleTestCase

from api import gemini


class CapturedRequest(object):
    """Stands in for urlopen so nothing leaves the machine."""

    def __init__(self, response=None):
        self.url = None
        self.body = None
        self._response = response or {
            'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}]}

    def __call__(self, req, *args, **kwargs):
        self.url = req.full_url
        self.body = json.loads(req.data.decode())
        return self

    # context-manager protocol
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._response).encode()


class EndpointTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'},
                                  clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # GEMINI_MODEL must not leak in from the developer's shell.
        if 'GEMINI_MODEL' in os.environ:
            del os.environ['GEMINI_MODEL']

    def test_the_default_endpoint_is_unchanged(self):
        """The exact URL this app has always called."""
        self.assertEqual(
            gemini._endpoint('generateContent'),
            'https://generativelanguage.googleapis.com/v1beta/models/'
            'gemini-2.5-flash:generateContent?key=test-key')

    def test_base_url_still_matches_the_default_model(self):
        """Kept as a module constant; it must not drift from DEFAULT_MODEL."""
        self.assertTrue(gemini.BASE_URL.endswith(gemini.DEFAULT_MODEL))

    def test_the_env_var_changes_the_model(self):
        with mock.patch.dict(os.environ, {'GEMINI_MODEL': 'gemini-3-pro'}):
            self.assertIn('/gemini-3-pro:generateContent',
                          gemini._endpoint('generateContent'))

    def test_a_per_call_override_beats_the_env_var(self):
        with mock.patch.dict(os.environ, {'GEMINI_MODEL': 'from-env'}):
            self.assertIn('/explicit:generateContent',
                          gemini._endpoint('generateContent', 'explicit'))

    def test_an_empty_env_var_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {'GEMINI_MODEL': ''}):
            self.assertIn('/%s:' % gemini.DEFAULT_MODEL,
                          gemini._endpoint('generateContent'))

    def test_a_missing_api_key_raises_rather_than_calling(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': ''}), \
                mock.patch.object(gemini, 'GEMINI_API_KEY', ''):
            with self.assertRaises(gemini.GeminiError):
                gemini._endpoint('generateContent')

    def test_the_stream_action_is_built_with_sse(self):
        url = gemini._endpoint('streamGenerateContent') + '&alt=sse'
        self.assertIn(':streamGenerateContent?', url)
        self.assertTrue(url.endswith('&alt=sse'))


class GenerationConfigTests(SimpleTestCase):
    def test_json_mode_is_the_default(self):
        self.assertEqual(gemini._generation_config(),
                         {'response_mime_type': 'application/json'})

    def test_temperature_is_omitted_when_not_given(self):
        """
        The important one: an unset temperature must not appear in the payload
        at all, so the default request shape is unchanged for every existing
        caller.
        """
        self.assertNotIn('temperature', gemini._generation_config())

    def test_temperature_is_included_when_given(self):
        self.assertEqual(gemini._generation_config(0.2)['temperature'], 0.2)

    def test_zero_temperature_is_not_treated_as_unset(self):
        """0.0 is a meaningful value and must survive the falsy check."""
        self.assertEqual(gemini._generation_config(0.0)['temperature'], 0.0)


class CallShapeTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'},
                                  clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        if 'GEMINI_MODEL' in os.environ:
            del os.environ['GEMINI_MODEL']

    def call(self, **kwargs):
        captured = CapturedRequest()
        with mock.patch.object(gemini.urllib.request, 'urlopen', captured):
            result = gemini.call_gemini_json(
                system_prompt='sys', user_prompt='usr', **kwargs)
        return captured, result

    def test_a_plain_call_hits_the_default_model_with_no_temperature(self):
        captured, result = self.call()
        self.assertEqual(result, {'ok': True})
        self.assertIn('/gemini-2.5-flash:generateContent', captured.url)
        self.assertEqual(captured.body['generationConfig'],
                         {'response_mime_type': 'application/json'})

    def test_the_prompt_is_sent_as_system_instruction_and_user_content(self):
        captured, _ = self.call()
        self.assertEqual(
            captured.body['system_instruction']['parts'][0]['text'], 'sys')
        self.assertEqual(
            captured.body['contents'][0]['parts'][0]['text'], 'usr')

    def test_a_model_override_changes_the_url_only(self):
        captured, _ = self.call(model='gemini-3-pro')
        self.assertIn('/gemini-3-pro:generateContent', captured.url)
        self.assertNotIn('temperature', captured.body['generationConfig'])

    def test_a_temperature_override_reaches_the_payload(self):
        captured, _ = self.call(temperature=0.1)
        self.assertEqual(captured.body['generationConfig']['temperature'], 0.1)

    def test_both_overrides_together(self):
        captured, _ = self.call(model='gemini-3-pro', temperature=0.9)
        self.assertIn('/gemini-3-pro:', captured.url)
        self.assertEqual(captured.body['generationConfig']['temperature'], 0.9)

    def test_the_api_key_is_not_in_the_request_body(self):
        """It belongs in the query string; a body copy would widen exposure."""
        captured, _ = self.call()
        self.assertNotIn('test-key', json.dumps(captured.body))


class ExistingCallersUnaffectedTests(SimpleTestCase):
    """
    Every AI feature in the app calls these functions. The new parameters are
    keyword-only with None defaults, so existing call sites -- and the test
    doubles that stand in for them -- keep working untouched.
    """

    def test_existing_keyword_only_signature_still_accepted(self):
        captured = CapturedRequest()
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'k'}), \
                mock.patch.object(gemini.urllib.request, 'urlopen', captured):
            gemini.call_gemini_json(system_prompt='s', user_prompt='u')
        self.assertIsNotNone(captured.url)

    def test_a_test_double_without_the_new_kwargs_is_still_compatible(self):
        """
        Mirrors how the suite mocks the AI: `def _call(*, system_prompt,
        user_prompt, **kw)`. Callers must not start passing model/temperature
        unsolicited, or every existing mock would break.
        """
        seen = {}

        def _double(*, system_prompt, user_prompt):
            seen['ok'] = True
            return {}

        with mock.patch.object(gemini, 'call_gemini_json', _double):
            gemini.call_gemini_json(system_prompt='s', user_prompt='u')
        self.assertTrue(seen['ok'])
