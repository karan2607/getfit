"""
The shared exercise guides must be unique, and the endpoint must survive the
rows that proved they were not.

`unique_together = ('user', 'name')` does not constrain them: the shared rows
carry user=NULL and Postgres treats NULLs as distinct. Two rows for one
exercise were legal, `update_or_create` raised MultipleObjectsReturned, and
the guide endpoint answered 503 -- which read, from outside, as an exercise
with no photo.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import ExerciseGuide, User


class GenericGuideUniquenessTests(TestCase):
    def guide(self, name):
        return ExerciseGuide.objects.create(
            user=None, name=name,
            data={'steps': ['a'], 'images': [], 'advice_version': 1})

    def test_two_shared_rows_for_one_exercise_are_refused(self):
        self.guide('Incline Dumbbell Press')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.guide('Incline Dumbbell Press')

    def test_a_case_variant_is_the_same_row(self):
        """Every read is `name__iexact`, so two cases are one guide."""
        self.guide('Incline Dumbbell Press')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.guide('incline dumbbell press')

    def test_a_users_own_copy_is_not_blocked_by_the_shared_one(self):
        """The constraint covers the shared rows only."""
        user = User.objects.create_user(email='g@test.local', password='x')
        self.guide('Incline Dumbbell Press')
        ExerciseGuide.objects.create(
            user=user, name='Incline Dumbbell Press',
            data={'steps': ['b'], 'images': []})
        self.assertEqual(
            ExerciseGuide.objects.filter(name='Incline Dumbbell Press').count(), 2)

    def test_two_users_may_each_have_their_own(self):
        a = User.objects.create_user(email='a@test.local', password='x')
        b = User.objects.create_user(email='b@test.local', password='x')
        for u in (a, b):
            ExerciseGuide.objects.create(
                user=u, name='Incline Dumbbell Press',
                data={'steps': ['c'], 'images': []})
        self.assertEqual(
            ExerciseGuide.objects.filter(name='Incline Dumbbell Press').count(), 2)


class GuideEndpointSurvivesDuplicatesTests(TestCase):
    """
    The endpoint answered 503 on a duplicated name. The constraint stops new
    duplicates; this is the read path proving it answers for the name that
    crashed, rather than a test of the constraint again.
    """

    def test_the_endpoint_answers_for_the_name_that_crashed(self):
        user = User.objects.create_user(email='ep@test.local', password='x')
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get(reverse('exercise-guide'),
                         {'name': 'Incline Dumbbell Press'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data.get('images'))
