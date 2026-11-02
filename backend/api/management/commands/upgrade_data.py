"""
Apply in-place data upgrades to existing workout plans.

Runs in the Render build after `migrate`, so a deploy repairs plans users already
created instead of requiring them to build new ones.

Usage:
    python manage.py upgrade_data            # apply pending steps to all plans
    python manage.py upgrade_data --dry-run  # report what would change
    python manage.py upgrade_data --force    # re-run every step (idempotent)
    python manage.py upgrade_data --plan <uuid>
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from api import data_upgrades
from api.models import WorkoutPlan


class Command(BaseCommand):
    help = 'Apply pending in-place data upgrades to existing workout plans.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing anything.')
        parser.add_argument(
            '--force', action='store_true',
            help='Re-run every upgrade step regardless of data_version.')
        parser.add_argument(
            '--plan', type=str, default=None,
            help='Only upgrade this plan id.')

    def _seed_library(self):
        """Re-derive the exercise library. Never fails the deploy."""
        from io import StringIO
        from django.core.management import call_command

        buffer = StringIO()
        try:
            call_command('seed_exercise_library', stdout=buffer)
        except Exception as exc:                        # noqa: BLE001
            # A library that is stale is survivable: unknown exercises warn
            # rather than block. A deploy that dies here is not.
            self.stdout.write(self.style.WARNING(
                'seed_exercise_library failed (continuing): %s' % exc))
            return
        summary = [line for line in buffer.getvalue().splitlines()
                   if 'created' in line]
        self.stdout.write('library: %s' % (summary[-1] if summary else 'seeded'))

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        # Seed the exercise library FIRST, and from here rather than from the
        # build command.
        #
        # `backend/render.yaml` is DOCUMENTATION -- both services were created
        # by hand in the Render dashboard, so the deployed build command lives
        # there and editing the blueprint changes nothing. Adding the seed to
        # render.yaml therefore had no effect at all, and staging ran with an
        # empty library: every exercise in a draft came back as "not in your
        # library", including Plank.
        #
        # upgrade_data is already in the deployed build command (the
        # data_version steps demonstrably run on every deploy), so hanging the
        # seed off it needs no dashboard change and cannot drift from the repo
        # again. It is idempotent and fast, and it runs before the plan loop so
        # a plan upgraded in this same pass can be checked against a current
        # library.
        if not dry_run:
            self._seed_library()

        plans = WorkoutPlan.objects.all().order_by('created_at')
        if options['plan']:
            plans = plans.filter(pk=options['plan'])
        if not force:
            plans = plans.filter(data_version__lt=data_upgrades.LATEST_VERSION)

        # Materialise the ids up front. Iterating a queryset lazily while
        # committing a transaction per plan invalidates the open cursor
        # ("Cursor needed to be reset because of commit/rollback").
        plan_ids = list(plans.values_list('pk', flat=True))

        total = len(plan_ids)
        self.stdout.write(
            'upgrade_data: target version %d, %d plan(s) to process%s'
            % (data_upgrades.LATEST_VERSION, total, ' [DRY RUN]' if dry_run else ''))
        if not total:
            self.stdout.write(self.style.SUCCESS('upgrade_data: nothing to do'))
            return

        plans_changed = 0
        rows_changed = 0
        failed = []

        for plan_id in plan_ids:
            plan = WorkoutPlan.objects.filter(pk=plan_id).first()
            if plan is None:
                continue   # deleted while we were working
            self.stdout.write('plan %s "%s" (v%d -> v%d)' % (
                plan.id, plan.title[:40], plan.data_version,
                data_upgrades.LATEST_VERSION))
            try:
                # One transaction per plan: a failure on one plan cannot leave
                # another half-upgraded, and the deploy keeps going.
                with transaction.atomic():
                    steps, rows = data_upgrades.upgrade_plan(
                        plan, stdout=self.stdout.write, force=force)
                    if dry_run:
                        transaction.set_rollback(True)
                if steps:
                    plans_changed += 1
                rows_changed += rows
            except Exception as exc:
                failed.append((str(plan_id), str(exc)))
                self.stderr.write(self.style.ERROR(
                    '  FAILED plan %s: %s' % (plan_id, exc)))

        self.stdout.write(
            'upgrade_data: %d plan(s) upgraded, %d row(s) changed'
            % (plans_changed, rows_changed))
        if failed:
            # Non-zero exit would fail the Render build; a data repair that
            # misses one plan should not block the deploy, so just report loudly.
            self.stderr.write(self.style.WARNING(
                'upgrade_data: %d plan(s) failed:' % len(failed)))
            for plan_id, err in failed:
                self.stderr.write('  %s: %s' % (plan_id, err))
        else:
            self.stdout.write(self.style.SUCCESS('upgrade_data: OK'))
