"""
Tests for dietitian document upload (PDF / photo).

Parse-and-discard: this project has no MEDIA_ROOT and no object storage, so the
uploaded bytes are read, sent to the vision model, and dropped. Only the
extracted text is retained. These tests assert the validation gate and that
nothing is written to disk.
"""

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from api import views, gemini
from api.models import User, UserProfile


PARSED = {
    'is_diet_document': True,
    'title': 'Cutting Plan',
    'target_calories': 2100,
    'protein_g': 180,
    'carbs_g': 190,
    'fat_g': 65,
    'meals_per_day': 4,
    'foods_to_include': ['paneer', 'oats'],
    'foods_to_avoid': ['fried food'],
    'restrictions': ['no beef'],
    'meal_timing': ['dinner before 8pm'],
    'notes': ['drink 3L water'],
    'summary': '2100 kcal, 180g protein. Avoid fried food. No beef.',
}


class DietDocumentParseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dd@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('diet-document-parse')
        self.captured = {}

    def stub(self, payload=None):
        """Patch the vision call at its source module (views import it locally)."""
        result = PARSED if payload is None else payload

        def _call(*, system_prompt, user_prompt, image_bytes, mime_type):
            self.captured['user_prompt'] = user_prompt
            self.captured['mime_type'] = mime_type
            self.captured['size'] = len(image_bytes)
            return result

        return mock.patch.multiple(
            gemini, call_gemini_vision_json=_call,
        ), mock.patch.object(views, 'call_gemini_vision_json', _call, create=True)

    def post(self, upload, payload=None, **extra):
        patches = self.stub(payload)
        with patches[0], patches[1]:
            data = {'document': upload}
            data.update(extra)
            return self.client.post(self.url, data, format='multipart')

    def pdf(self, name='plan.pdf', content=b'%PDF-1.4 fake'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def jpg(self, name='plan.jpg', content=b'\xff\xd8\xff fake'):
        return SimpleUploadedFile(name, content, content_type='image/jpeg')

    # -- happy paths --------------------------------------------------------

    def test_pdf_is_accepted_and_parsed(self):
        res = self.post(self.pdf())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['target_calories'], 2100)
        self.assertEqual(res.data['summary'], PARSED['summary'])
        self.assertEqual(self.captured['mime_type'], 'application/pdf')

    def test_image_is_accepted(self):
        res = self.post(self.jpg())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.captured['mime_type'], 'image/jpeg')

    def test_image_field_name_also_works(self):
        patches = self.stub()
        with patches[0], patches[1]:
            res = self.client.post(self.url, {'image': self.jpg()},
                                   format='multipart')
        self.assertEqual(res.status_code, 200, res.data)

    def test_prompt_states_the_users_unit(self):
        self.post(self.pdf())
        self.assertIn('in lb', self.captured['user_prompt'])

    def test_prompt_forbids_inventing_values(self):
        """A dietitian's plan must be transcribed, not embellished."""
        self.post(self.pdf())
        prompt = self.captured['user_prompt']
        self.assertIn('Do NOT invent', prompt)
        self.assertIn('return null', prompt)

    # -- validation ---------------------------------------------------------

    def test_missing_file_is_rejected(self):
        res = self.client.post(self.url, {}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_unsupported_type_is_rejected(self):
        bad = SimpleUploadedFile('notes.docx', b'PK fake',
                                 content_type='application/vnd.openxmlformats'
                                              '-officedocument.wordprocessingml'
                                              '.document')
        res = self.client.post(self.url, {'document': bad}, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Unsupported', res.data['detail'])

    def test_executable_is_rejected(self):
        bad = SimpleUploadedFile('x.sh', b'#!/bin/sh\nrm -rf /',
                                 content_type='application/x-sh')
        res = self.client.post(self.url, {'document': bad}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_empty_file_is_rejected(self):
        empty = SimpleUploadedFile('plan.pdf', b'', content_type='application/pdf')
        res = self.client.post(self.url, {'document': empty}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_non_diet_document_is_rejected_with_a_clear_message(self):
        res = self.post(self.pdf(), payload={'is_diet_document': False,
                                             'summary': ''})
        self.assertEqual(res.status_code, 400)
        self.assertIn("doesn't look like a diet plan", res.data['detail'])

    def test_unexpected_ai_format_is_a_502(self):
        res = self.post(self.pdf(), payload=['not', 'a', 'dict'])
        self.assertEqual(res.status_code, 502)

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(self.url, {'document': self.pdf()},
                               format='multipart')
        self.assertIn(res.status_code, (401, 403))

    # -- profile persistence ------------------------------------------------

    def test_not_saved_to_profile_by_default(self):
        self.post(self.pdf())
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.personal_notes, '')

    def test_save_to_profile_when_asked(self):
        res = self.post(self.pdf(), save_to_profile='true')
        self.assertTrue(res.data.get('saved_to_profile'))
        self.user.profile.refresh_from_db()
        self.assertIn('Dietitian plan:', self.user.profile.personal_notes)
        self.assertIn('2100 kcal', self.user.profile.personal_notes)

    def test_resaving_replaces_rather_than_appending_forever(self):
        self.post(self.pdf(), save_to_profile='true')
        self.post(self.pdf(), save_to_profile='true')
        self.user.profile.refresh_from_db()
        notes = self.user.profile.personal_notes
        self.assertEqual(notes.count('Dietitian plan:'), 1,
                         'repeated uploads must not stack up blocks')

    def test_existing_unrelated_notes_are_preserved(self):
        self.user.profile.personal_notes = 'Left shoulder impingement.'
        self.user.profile.save()
        self.post(self.pdf(), save_to_profile='true')
        self.user.profile.refresh_from_db()
        self.assertIn('Left shoulder impingement.',
                      self.user.profile.personal_notes)
        self.assertIn('Dietitian plan:', self.user.profile.personal_notes)

    # -- no persistence of the file itself ----------------------------------

    def test_nothing_is_written_to_disk(self):
        """
        There is no MEDIA_ROOT in this project; the upload must stay in memory.
        Asserted by confirming the default storage was never touched.
        """
        from django.core.files.storage import default_storage
        with mock.patch.object(default_storage, 'save') as saved:
            self.post(self.pdf())
        saved.assert_not_called()


class DietPlanGenerateWithNotesTests(APITestCase):
    """The extracted instructions must actually reach the plan generator."""

    def setUp(self):
        self.user = User.objects.create_user(email='dg@test.local', password='x')
        UserProfile.objects.create(user=self.user, preferred_unit='lb',
                                   fitness_goal='lose_fat',
                                   dietary_preference='veg')
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.url = reverse('diet-plan-generate')
        self.captured = {}

    def generate(self, **extra):
        def _call(*, system_prompt, user_prompt):
            self.captured['user_prompt'] = user_prompt
            return {'title': 'P', 'description': '', 'meals': []}

        with mock.patch.object(gemini, 'call_gemini_json', _call), \
                mock.patch.object(views, 'call_gemini_json', _call):
            return self.client.post(self.url, extra, format='json')

    def test_dietitian_notes_reach_the_prompt(self):
        res = self.generate(dietitian_notes='No beef. 2100 kcal.')
        self.assertEqual(res.status_code, 200, res.data)
        prompt = self.captured['user_prompt']
        self.assertIn('No beef. 2100 kcal.', prompt)

    def test_dietitian_notes_are_marked_as_taking_precedence(self):
        self.generate(dietitian_notes='No beef.')
        self.assertIn('PRECEDENCE', self.captured['user_prompt'])

    def test_absent_notes_leave_the_prompt_unchanged(self):
        self.generate()
        self.assertNotIn('PRECEDENCE', self.captured['user_prompt'])

    def test_overlong_notes_are_truncated(self):
        self.generate(dietitian_notes='x' * 9000)
        self.assertLess(self.captured['user_prompt'].count('x'), 5000)
