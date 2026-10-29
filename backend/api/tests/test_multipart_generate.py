"""
Plan generation with a body photo attached.

Reported from a phone: "Failed to build prompt: string indices must be
integers, not 'str'".

The mechanism, which is worth stating exactly because the shape of the bug is
the whole lesson. The client sends JSON normally, but switches to FormData when
a body photo is attached (lib/api.ts), and FormData cannot carry nested
structures -- so it JSON.stringify()s `specific_goals`, `goal_params` and
`confirmed_target`. The server never parsed them back, so `goal_params` arrived
as the STRING '{"lose_weight": {...}}'. Then:

    if 'lose_weight' in goal_params      # substring match on a str -> True!
        p = goal_params['lose_weight']   # TypeError

The `in` test passing on a string is what made this a 500 rather than a quietly
skipped branch. Two silent corruptions rode along with it: `specific_goals` as
a string iterates CHARACTERS, putting "l, o, s, e" into the prompt, and
`confirmed_target` as a string has no `.get`.

So: attaching a photo broke plan generation, and only when a photo was
attached, which is why it never showed up in testing.
"""

import io
import json
from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views
from api.models import User, UserProfile


def _png():
    """A one-pixel PNG, enough to be a real uploaded file."""
    return io.BytesIO(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
        b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


class JsonFieldCoercionTests(APITestCase):
    """The helper, in isolation."""

    def test_a_json_string_is_parsed(self):
        self.assertEqual(
            views._json_field('{"lose_weight": {"amount": 5}}', dict),
            {'lose_weight': {'amount': 5}})
        self.assertEqual(views._json_field('["a", "b"]', list), ['a', 'b'])

    def test_an_already_parsed_value_passes_through(self):
        self.assertEqual(views._json_field({'a': 1}, dict), {'a': 1})
        self.assertEqual(views._json_field(['a'], list), ['a'])

    def test_the_wrong_container_is_rejected(self):
        """A list where a dict belongs is not usable, so it is dropped."""
        self.assertIsNone(views._json_field('["a"]', dict))
        self.assertIsNone(views._json_field('{"a": 1}', list))

    def test_unparseable_input_is_dropped_not_raised(self):
        """
        Dropped rather than 500. These fields only sharpen the prompt, so a
        malformed one should cost that sharpening -- never the plan.
        """
        for junk in ('not json', '', '{', 'null', '42', None, 42, object()):
            self.assertIsNone(views._json_field(junk, dict), msg=repr(junk))

    def test_a_bare_json_string_is_not_mistaken_for_a_container(self):
        """'"lose_weight"' parses to a str, which is the original bug's shape."""
        self.assertIsNone(views._json_field('"lose_weight"', dict))


class MultipartGenerateTests(APITestCase):
    """End to end, the way the phone actually sends it."""

    def setUp(self):
        self.user = User.objects.create_user(email='mp@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    FAKE_PLAN = {
        'title': 'Generated',
        'duration_weeks': 4,
        'week': [{'day_number': 1, 'name': 'Push', 'is_rest_day': False,
                  'exercises': [{'name': 'Push-ups', 'sets': 3, 'reps': '15'}]}],
    }

    def post_multipart(self, **extra):
        """Exactly the client's photo path: stringified nested fields."""
        payload = {
            'days_per_week': '4',
            'duration_weeks': '4',
            'fitness_goal': 'lose_fat',
            'experience_level': 'beginner',
            # JSON.stringify'd, as FormData forces
            'specific_goals': json.dumps(['lose_weight', 'build_muscle']),
            'goal_params': json.dumps(
                {'lose_weight': {'amount': 5, 'unit': 'kg'}}),
            'confirmed_target': json.dumps(
                {'label': 'Body weight', 'recommended_value': 70,
                 'current_value': 75, 'unit': 'kg'}),
        }
        payload.update(extra)
        photo = _png()
        photo.name = 'body.png'
        payload['body_photo'] = photo
        with patch('api.gemini.call_gemini_json',
                   return_value=dict(self.FAKE_PLAN)), \
             patch('api.gemini.call_gemini_vision_text', return_value=''):
            return self.client.post(reverse('workout-plan-generate'),
                                    payload, format='multipart')

    def test_generating_with_a_photo_no_longer_500s(self):
        """THE reported bug: 'string indices must be integers, not str'."""
        res = self.post_multipart()
        self.assertNotEqual(res.status_code, 500,
                            msg=res.data.get('detail') if hasattr(res, 'data') else res)
        self.assertEqual(res.status_code, 200, res.data)

    def test_the_stringified_goal_params_actually_reach_the_prompt(self):
        """
        Not just "does not crash" -- the values have to be USED. A fix that
        swallowed the string would turn a 500 into a silently worse plan.
        """
        captured = {}

        def _capture(system_prompt, user_prompt, **kwargs):
            captured['user'] = user_prompt
            return dict(self.FAKE_PLAN)

        photo = _png()
        photo.name = 'body.png'
        with patch('api.gemini.call_gemini_json', side_effect=_capture), \
             patch('api.gemini.call_gemini_vision_text', return_value=''):
            res = self.client.post(reverse('workout-plan-generate'), {
                'days_per_week': '4', 'duration_weeks': '4',
                'specific_goals': json.dumps(['lose_weight']),
                'goal_params': json.dumps(
                    {'lose_weight': {'amount': 5, 'unit': 'kg'}}),
                'body_photo': photo,
            }, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('lose 5 kg', captured['user'])

    def test_goals_are_not_iterated_character_by_character(self):
        """
        The silent corruption that rode along with the crash: a string
        `specific_goals` iterates CHARACTERS, so the prompt read
        "Specific goals: l, o, s, e, _, w, ...".
        """
        captured = {}

        def _capture(system_prompt, user_prompt, **kwargs):
            captured['user'] = user_prompt
            return dict(self.FAKE_PLAN)

        photo = _png()
        photo.name = 'body.png'
        with patch('api.gemini.call_gemini_json', side_effect=_capture), \
             patch('api.gemini.call_gemini_vision_text', return_value=''):
            self.client.post(reverse('workout-plan-generate'), {
                'days_per_week': '4', 'duration_weeks': '4',
                'specific_goals': json.dumps(['build_muscle']),
                'body_photo': photo,
            }, format='multipart')
        self.assertIn('build muscle', captured['user'])
        self.assertNotIn('b, u, i, l, d', captured['user'])

    def test_a_malformed_nested_field_does_not_break_generation(self):
        """
        An old client, or a hand-rolled request, sending junk must degrade to a
        slightly less specific prompt rather than a 500.
        """
        res = self.post_multipart(goal_params='not json at all',
                                  specific_goals='also not json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_the_json_path_still_works(self):
        """The regression guard: no photo, nested dicts, as the web app sends."""
        with patch('api.gemini.call_gemini_json',
                   return_value=dict(self.FAKE_PLAN)):
            res = self.client.post(reverse('workout-plan-generate'), {
                'days_per_week': 4, 'duration_weeks': 4,
                'specific_goals': ['lose_weight'],
                'goal_params': {'lose_weight': {'amount': 5, 'unit': 'kg'}},
                'confirmed_target': {'label': 'Body weight',
                                     'recommended_value': 70},
            }, format='json')
        self.assertEqual(res.status_code, 200, res.data)


class MultipartSuggestTargetTests(APITestCase):
    """
    The same two lines exist in workout_plan_suggest_target, reading the same
    fields. It is not reachable with a photo today, but the duplicated unguarded
    subscript is, so it gets the same coercion rather than waiting to be
    reported separately.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='mp2@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='kg')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_stringified_params_do_not_500(self):
        fake = {'program_target': {'metric': 'weight', 'label': 'Body weight',
                                   'recommended_value': 70},
                'message': 'ok'}
        with patch('api.gemini.call_gemini_json', return_value=fake):
            res = self.client.post(reverse('workout-plan-suggest-target'), {
                'days_per_week': '4', 'duration_weeks': '4',
                'specific_goals': json.dumps(['lose_weight']),
                'goal_params': json.dumps(
                    {'lose_weight': {'amount': 5, 'unit': 'kg'}}),
            }, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
