"""
The staging safety guard.

Staging points at a Neon BRANCH of production, so the whole point is to run a
row-mutating migration against real-shaped data before promoting it. The
danger is not the branching -- it is running that against the wrong database.

So `reset_staging` refuses unless it can PROVE it is not production, and these
tests are about the refusals, not the happy path. An unset
`PRODUCTION_DB_HOST` is treated as "cannot prove it is safe", because an unset
variable is the most likely way to end up pointed at production by accident.
"""

from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


# Distinctive enough that assertNotIn is actually meaningful. A single-letter
# password would make the assertion pass by luck, since one letter appears
# somewhere in almost any output.
FAKE_PASSWORD = 'npg_SeNtInElPaSsWoRd123'
FAKE_USER = 'neondb_owner_sentinel'


def _pg(host, name='neondb'):
    """A DATABASES dict that looks like Postgres on `host`."""
    return {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': host, 'NAME': name,
        'USER': FAKE_USER, 'PASSWORD': FAKE_PASSWORD, 'PORT': 5432,
    }}


class ResetStagingGuardTests(TestCase):
    def run_cmd(self, **opts):
        call_command('reset_staging', **opts)

    def test_it_refuses_on_local_sqlite(self):
        """
        The default local database. Running the staging tooling against it is
        always a mistake, and the message should say which mistake.
        """
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd(check=True)
        self.assertIn('SQLite', str(ctx.exception))

    def test_it_refuses_when_the_production_host_is_unknown(self):
        """
        THE important refusal. Without PRODUCTION_DB_HOST the command cannot
        prove anything, and "I could not check" must never read as "it is
        fine".
        """
        with mock.patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('PRODUCTION_DB_HOST', None)
            with self.settings(DATABASES=_pg('ep-staging-xyz.neon.tech')):
                with self.assertRaises(CommandError) as ctx:
                    self.run_cmd(check=True)
        self.assertIn('PRODUCTION_DB_HOST', str(ctx.exception))

    def test_it_refuses_when_pointed_at_production(self):
        prod = 'ep-wispy-prod-123-pooler.neon.tech'
        with mock.patch.dict('os.environ', {'PRODUCTION_DB_HOST': prod}):
            with self.settings(DATABASES=_pg(prod)):
                with self.assertRaises(CommandError) as ctx:
                    self.run_cmd(check=True)
        self.assertIn('PRODUCTION', str(ctx.exception))

    def test_it_proceeds_on_a_different_host(self):
        with mock.patch.dict('os.environ',
                             {'PRODUCTION_DB_HOST': 'ep-prod-1.neon.tech'}):
            with self.settings(DATABASES=_pg('ep-staging-2.neon.tech')):
                self.run_cmd(check=True)      # must not raise

    def test_it_does_not_print_credentials(self):
        """
        The command reports which database it is on, and that report must not
        leak the password or the full host. A connection string pasted into a
        log or a screenshot is how credentials escape.
        """
        from io import StringIO
        out = StringIO()
        with mock.patch.dict('os.environ',
                             {'PRODUCTION_DB_HOST': 'ep-prod-1.neon.tech'}):
            with self.settings(DATABASES=_pg('ep-staging-2.neon.tech')):
                call_command('reset_staging', check=True, stdout=out)
        printed = out.getvalue()
        self.assertNotIn(FAKE_PASSWORD, printed)
        self.assertNotIn(FAKE_USER, printed)
        # The host is truncated to its leading label, which identifies a Neon
        # branch endpoint without reproducing anything pasteable.
        self.assertNotIn('neon.tech', printed)
        self.assertIn('ep-staging-2', printed)
        self.assertIn('NOT the production host', printed)
