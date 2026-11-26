"""
The health endpoint says which commit is answering.

A staging deploy stopped firing for an hour while pushes kept landing on
GitHub. Every "live verification" against it was really re-testing the old
build, and nothing in the response said so. A live check is only evidence if
you know what it ran against.
"""
import os
from unittest import mock

from django.test import TestCase
from django.urls import reverse


class HealthCommitTests(TestCase):
    def test_it_reports_the_deployed_commit(self):
        with mock.patch.dict(os.environ,
                             {'RENDER_GIT_COMMIT': 'abc1234def5678'}):
            res = self.client.get(reverse('health'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'ok')
        self.assertEqual(res.data['commit'], 'abc1234')

    def test_it_stays_up_where_the_commit_is_unknown(self):
        """Blank off Render, rather than a wrong or invented value."""
        env = {k: v for k, v in os.environ.items() if k != 'RENDER_GIT_COMMIT'}
        with mock.patch.dict(os.environ, env, clear=True):
            res = self.client.get(reverse('health'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['commit'], '')


class HealthDatabaseTests(TestCase):
    """
    Health answered a confident "ok" in 0.15s on 26 Nov while real requests
    took thirty seconds, because it touched nothing. A check that cannot
    fail is not a check.
    """

    def test_it_reports_the_database(self):
        res = self.client.get(reverse('health'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['database'], 'ok')

    def test_a_database_that_is_down_is_reported_not_hidden(self):
        from django.db import connection

        with mock.patch.object(connection, 'cursor',
                               side_effect=RuntimeError('no route to host')):
            res = self.client.get(reverse('health'))
        self.assertEqual(res.data['database'], 'unreachable')

    def test_it_still_answers_200_when_the_database_is_down(self):
        """
        Render restarts a service whose health check fails. A brief blip is
        not a reason to throw away a warm process; the body says what is
        wrong.
        """
        from django.db import connection

        with mock.patch.object(connection, 'cursor',
                               side_effect=RuntimeError('no route to host')):
            res = self.client.get(reverse('health'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'ok')
