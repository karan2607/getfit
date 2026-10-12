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

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

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
