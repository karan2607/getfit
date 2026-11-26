"""
Database connections are reused between requests.

Measured against production on 26 Nov: /api/health/, which touched no
database, answered in 0.15s while /api/profile/, which makes one query,
took 0.79s -- consistently, warm. That 0.63s is the TLS and auth handshake
to Neon, and without connection reuse it is paid on EVERY request. A screen
that makes ten calls spends six seconds reconnecting.

This is a settings guard rather than a behaviour test, because the defect
is a default nobody set and the symptom is latency, which a unit test
cannot see.
"""
import os
from unittest import mock

from django.test import SimpleTestCase


class ConnectionReuseTests(SimpleTestCase):
    def config(self, env):
        """The DATABASES dict settings.py builds for a given environment."""
        import importlib.util

        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), 'config',
            'settings.py')
        spec = importlib.util.spec_from_file_location('_probe_settings', path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, env, clear=False):
            spec.loader.exec_module(module)
        return module.DATABASES['default']

    def test_postgres_holds_its_connection_open(self):
        db = self.config({
            'DATABASE_URL': 'postgres://u:p@host.neon.tech:5432/neondb',
            'DEBUG': 'False',
        })
        self.assertGreaterEqual(db['CONN_MAX_AGE'], 60)

    def test_a_suspended_database_is_detected_rather_than_served_dead(self):
        """
        Neon suspends an idle compute, which leaves a pooled connection
        dead. This is the whole risk of reusing connections, and Django
        handles it -- but only when asked.
        """
        db = self.config({
            'DATABASE_URL': 'postgres://u:p@host.neon.tech:5432/neondb',
            'DEBUG': 'False',
        })
        self.assertTrue(db['CONN_HEALTH_CHECKS'])

    def test_it_can_be_turned_off_without_a_deploy(self):
        db = self.config({
            'DATABASE_URL': 'postgres://u:p@host.neon.tech:5432/neondb',
            'DEBUG': 'False', 'CONN_MAX_AGE': '0',
        })
        self.assertEqual(db['CONN_MAX_AGE'], 0)
