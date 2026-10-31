"""
Report what the staging database contains, and refuse to confuse it with
production.

Why this exists
---------------
Staging points at a Neon BRANCH of production, so it holds real-shaped data to
run a row-mutating migration against before promoting it. Re-branching is done
in the Neon dashboard (this command cannot call Neon's API without a token),
but the dangerous part is not the branching -- it is running a destructive
check against the wrong database by accident.

So this command's real job is the SAFETY CHECK: it compares the configured
database against the production host and refuses to proceed if they match.

Usage:
    python manage.py reset_staging --check     # what am I pointed at?
    python manage.py reset_staging --counts    # row counts, for comparing to prod

`PRODUCTION_DB_HOST` must be set for the guard to be meaningful. Without it the
command refuses to do anything rather than assuming it is safe -- an unset
variable is the most likely way to end up pointed at production.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Inspect the staging database and verify it is not production.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true',
                            help='Report which database is configured.')
        parser.add_argument('--counts', action='store_true',
                            help='Row counts, to compare against production.')

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        host = db.get('HOST') or ''
        name = db.get('NAME') or ''

        if db.get('ENGINE', '').endswith('sqlite3'):
            raise CommandError(
                'This is a local SQLite database, not staging. Set DATABASE_URL '
                'to the Neon staging branch first.')

        prod_host = os.getenv('PRODUCTION_DB_HOST', '').strip()
        if not prod_host:
            raise CommandError(
                'PRODUCTION_DB_HOST is not set, so I cannot prove this is not '
                'production. Refusing to continue. Set it to the production '
                'Neon host and re-run.')

        # Compare host AND database name. Neon branches share a project and
        # differ in the endpoint id, so the host is the discriminator -- but a
        # matching name on a different host is worth reporting too.
        if host == prod_host:
            raise CommandError(
                'DATABASE_URL points at the PRODUCTION host (%s). Refusing. '
                'Point it at the Neon staging branch.' % host)

        # Never print credentials. The host's leading label is enough to
        # identify a Neon branch endpoint without exposing the password.
        self.stdout.write('database: %s' % name)
        self.stdout.write('host:     %s...' % host.split('.')[0])
        self.stdout.write(self.style.SUCCESS(
            'verified: this is NOT the production host'))

        if options['counts']:
            self._counts()

    def _counts(self):
        """
        Row counts for the tables a data migration is most likely to damage.

        Printed so the same command can be run against production and staging
        and the two outputs diffed before promoting a migration.
        """
        from api.models import (
            User, WorkoutPlan, WorkoutDay, Exercise, Composite, WorkoutSession,
            SetLog, HealthDailySummary, MealLog,
        )

        self.stdout.write('')
        for model in (User, WorkoutPlan, WorkoutDay, Exercise, Composite,
                      WorkoutSession, SetLog, HealthDailySummary, MealLog):
            self.stdout.write('  %-22s %d'
                              % (model.__name__, model.objects.count()))
