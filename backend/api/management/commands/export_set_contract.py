"""
Write the measurement-type contract to a JSON file the frontend tests read.

Why this exists
---------------
`frontend/src/lib/units.ts` mirrors `exercise_types.FIELD_CONTRACT` by hand,
and it drifted. `cardio_machine` and `weight_distance` were added server-side,
upgrade step 6 reclassified 40 existing rows onto them, and the frontend union
still listed five types. So `fieldsFor('cardio_machine')` missed its lookup,
fell back to `weight_reps`, and a treadmill rendered weight and reps inputs
whose values the server then discarded on save.

A hand-written mirror with a comment saying "keep in step" is not a mechanism.
This is: the backend emits the contract, the frontend test asserts against it,
and CI regenerates the file and fails if it changed — so a backend change that
forgets the frontend breaks the build instead of shipping.

Usage:
    python manage.py export_set_contract            # write the file
    python manage.py export_set_contract --check     # fail if it is stale
"""

import json
import os

from django.core.management.base import BaseCommand, CommandError

from api import exercise_types


# Relative to the repo root, which is the parent of BASE_DIR.
OUTPUT = os.path.join('frontend', 'src', 'test', 'contract.json')


def build():
    """The contract, as the frontend needs to see it."""
    return {
        'types': list(exercise_types.ALL_TYPES),
        'fields': list(exercise_types.ALL_SET_FIELDS),
        'field_contract': {
            t: list(exercise_types.FIELD_CONTRACT[t])
            for t in exercise_types.ALL_TYPES
        },
        'chart_contract': {
            t: exercise_types.CHART_CONTRACT[t]
            for t in exercise_types.ALL_TYPES
        },
    }


def serialise():
    # sort_keys so regenerating never produces a spurious diff from dict order.
    return json.dumps(build(), indent=2, sort_keys=True) + '\n'


class Command(BaseCommand):
    help = 'Export the measurement-type contract for the frontend tests.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check', action='store_true',
            help='Exit non-zero if the committed file is out of date.')

    def handle(self, *args, **options):
        from django.conf import settings
        repo_root = os.path.dirname(settings.BASE_DIR)
        path = os.path.join(repo_root, OUTPUT)
        payload = serialise()

        if options['check']:
            try:
                with open(path) as handle:
                    current = handle.read()
            except (IOError, OSError):
                raise CommandError(
                    '%s is missing. Run `manage.py export_set_contract`.'
                    % OUTPUT)
            if current != payload:
                raise CommandError(
                    '%s is out of date — the server contract changed and the '
                    'frontend was not told. Run '
                    '`manage.py export_set_contract` and commit the result.'
                    % OUTPUT)
            self.stdout.write(self.style.SUCCESS('%s is up to date' % OUTPUT))
            return

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(payload)
        self.stdout.write('wrote %s (%d types, %d fields)'
                          % (OUTPUT, len(exercise_types.ALL_TYPES),
                             len(exercise_types.ALL_SET_FIELDS)))
